"""受限读取已授权任务中的文本文件，不修改文件或数据库。"""

import errno
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from app.services.workspace.directory.workspace_path import resolve_task_workspace_path


# 限制由服务端决定，暂不允许模型自行扩大。
MAX_TEXT_FILE_BYTES = 256 * 1024
READ_CHUNK_BYTES = 64 * 1024

WorkspaceFileErrorCode = Literal[
    "file_read_unsupported",
    "file_not_regular",
    "file_too_large",
    "file_not_utf8_text",
    "file_changed",
    "file_not_found",
    "file_access_denied",
    "file_unavailable",
]


class WorkspaceFileError(ValueError):
    """只提供安全文案与稳定错误码，不反射底层路径或文件内容。"""

    def __init__(
        self,
        code: WorkspaceFileErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceTextFile:
    """普通结果对象，不携带文件句柄或本机绝对路径。"""

    relative_path: str
    content: str
    byte_count: int


def _require_supported_file_access() -> None:
    """能力不足时拒绝，不退回存在跟随链接风险的普通路径打开。"""

    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")

    if (
        os.name != "posix"
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
        or not all(hasattr(os, name) for name in required_flags)
    ):
        raise WorkspaceFileError(
            "file_read_unsupported",
            "当前平台尚不支持受限文件读取",
        )


def _check_regular_file(metadata: os.stat_result) -> None:
    """拒绝目录、链接、管道、设备等非普通文件。"""

    if not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceFileError(
            "file_not_regular",
            "只能读取普通文本文件",
        )


def _file_version(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    """记录对象身份与常用变化信号；不把它当作原子文件快照。"""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_bounded_bytes(descriptor: int) -> bytes:
    """从已打开的普通文件读取，实际读取量始终有上限。"""

    chunks: list[bytes] = []
    total = 0

    # 多读一个字节用于识别超限；不能只信任打开前记录的文件大小。
    while total <= MAX_TEXT_FILE_BYTES:
        remaining = MAX_TEXT_FILE_BYTES + 1 - total
        chunk = os.read(
            descriptor,
            min(READ_CHUNK_BYTES, remaining),
        )

        if not chunk:
            break

        chunks.append(chunk)
        total += len(chunk)

    if total > MAX_TEXT_FILE_BYTES:
        raise WorkspaceFileError(
            "file_too_large",
            "文件超过允许读取的大小",
        )

    return b"".join(chunks)


def _read_resolved_file(path: Path) -> bytes:
    """逐级打开规范绝对路径，不跟随解析后新出现的符号链接。"""

    if not path.is_absolute() or ".." in path.parts:
        raise WorkspaceFileError(
            "file_unavailable",
            "文件路径无法用于受限读取",
        )

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

    # 由入口确保平台能力满足要求。
    # 从文件系统根逐段打开，也保护 Workspace 根目录的祖先路径。
    # ExitStack 按逆序关闭所有描述符，异常分支也会清理。
    with ExitStack() as stack:
        parent_fd = os.open(path.anchor, directory_flags)
        stack.callback(os.close, parent_fd)

        for component in path.parts[1:-1]:
            parent_fd = os.open(
                component,
                directory_flags,
                dir_fd=parent_fd,
            )
            stack.callback(os.close, parent_fd)

        filename = path.name

        # 不跟随末端链接，先拒绝已知的特殊文件。
        # 这次检查与 open 之间仍可能变化，因此打开后必须再检查。
        before_open = os.stat(
            filename,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        _check_regular_file(before_open)

        if before_open.st_size > MAX_TEXT_FILE_BYTES:
            raise WorkspaceFileError(
                "file_too_large",
                "文件超过允许读取的大小",
            )

        # O_NONBLOCK 避免目标被换成 FIFO 后在打开时等待写入者。
        # 对普通文件不提供读取超时，也不能替代后续运行时资源管理。
        file_fd = os.open(
            filename,
            file_flags,
            dir_fd=parent_fd,
        )
        stack.callback(os.close, file_fd)

        opened = os.fstat(file_fd)
        _check_regular_file(opened)

        # 检查实际打开的文件是否与打开前观察到的对象一致。
        if _file_version(opened) != _file_version(before_open):
            raise WorkspaceFileError(
                "file_changed",
                "文件在打开期间发生变化，请重新读取",
            )

        data = _read_bounded_bytes(file_fd)
        after_read = os.fstat(file_fd)

        # 检测常见的读取期间修改，不承诺并发写入下的原子快照。
        if (
            _file_version(after_read) != _file_version(opened)
            or len(data) != after_read.st_size
        ):
            raise WorkspaceFileError(
                "file_changed",
                "文件在读取期间发生变化，请重新读取",
            )

        return data


def read_task_text_file(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str,
    expected_bound_root: str | None = None,
) -> WorkspaceTextFile:
    """读取已授权任务内的 UTF-8 文本，身份与任务定位由服务端提供。"""

    # 复用上一课的完整授权链路。
    # 返回时数据库 Session 已关闭，下面的文件读取不占用数据库事务。
    # 可选绑定约束来自内部提案快照，不开放给模型参数。
    binding = {} if expected_bound_root is None else {"expected_bound_root": expected_bound_root}
    path = resolve_task_workspace_path(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=relative_path,
        **binding,
    )

    _require_supported_file_access()

    try:
        data = _read_resolved_file(path)

    except FileNotFoundError:
        raise WorkspaceFileError(
            "file_not_found",
            "文件或所在目录已不存在",
        ) from None

    except PermissionError:
        raise WorkspaceFileError(
            "file_access_denied",
            "没有权限读取目标文件",
        ) from None

    except OSError as error:
        # 打开阶段拒绝链接或中间项已不再是目录，统一视为路径变化。
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise WorkspaceFileError(
                "file_changed",
                "文件路径在打开期间发生变化，请重新读取",
            ) from None

        raise WorkspaceFileError(
            "file_unavailable",
            "目标文件暂时无法读取",
        ) from None

    # 本课接受严格 UTF-8 且不含 NUL 的内容，不猜测其他编码。
    if b"\x00" in data:
        raise WorkspaceFileError(
            "file_not_utf8_text",
            "只支持不含 NUL 字节的 UTF-8 文本文件",
        )

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkspaceFileError(
            "file_not_utf8_text",
            "文件不是有效的 UTF-8 文本",
        ) from None

    return WorkspaceTextFile(
        # 路径已经通过上一课校验，只返回规范化后的相对输入。
        relative_path=PurePosixPath(relative_path).as_posix(),
        content=content,
        byte_count=len(data),
    )
