"""内部单文件替换原语；不负责数据库授权、审批或执行占用。"""

import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.services.workspace.files.workspace_file import (
    MAX_TEXT_FILE_BYTES,
    WorkspaceFileError,
    _file_version,
    _read_bounded_bytes,
)
from app.services.workspace.directory.workspace_path import (
    WorkspacePathError,
    _parse_relative_path,
)
from app.services.workspace.metadata.workspace_file_metadata import (
    FileMetadataError,
    copy_file_metadata,
    read_file_metadata,
    verify_file_metadata,
)

ReplaceStatus = Literal["not_replaced", "replaced", "uncertain"]


@dataclass(frozen=True)
class FileReplaceResult:
    """仅描述本次文件操作，不代表数据库应用状态已经登记。"""

    status: ReplaceStatus
    code: str

    # 清理失败独立表达，不能覆盖“文件可能已经替换”的事实。
    cleanup_complete: bool


class FileReplaceRejected(ValueError):
    """固定错误分类，不携带文件正文或宿主路径。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require_supported_platform() -> None:
    """平台能力不足时拒绝，不退回普通字符串路径写入。"""

    required_flags = (
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    )
    required_functions = (
        "fchmod",
        "fchown",
        "geteuid",
        "replace",
        "fsync",
    )

    if (
        os.name != "posix"
        or not all(hasattr(os, name) for name in required_flags)
        or not all(hasattr(os, name) for name in required_functions)
        or not all(
            function in os.supports_dir_fd
            for function in (os.open, os.stat, os.unlink, os.rename)
        )
        or os.stat not in os.supports_follow_symlinks
    ):
        raise FileReplaceRejected("file_replace_unsupported")


def _validate_content(content: str, expected_sha256: str) -> bytes:
    """验证完整新内容，不从Diff重新拼接或归一化文本。"""

    if (
        not isinstance(content, str)
        or "\x00" in content
        or len(content) > MAX_TEXT_FILE_BYTES
        or not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise FileReplaceRejected("file_replace_content_invalid")

    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError:
        raise FileReplaceRejected(
            "file_replace_content_invalid",
        ) from None

    if (
        len(encoded) > MAX_TEXT_FILE_BYTES
        or sha256(encoded).hexdigest() != expected_sha256
    ):
        raise FileReplaceRejected("file_replace_content_invalid")

    return encoded


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    """对象身份检查，不把相同路径字符串当作相同目录对象。"""

    return metadata.st_dev, metadata.st_ino


def _verify_directory_chain(
    links: list[tuple[int, str, int]],
) -> None:
    """检查打开过的目录项是否仍指向原描述符。"""

    for parent_fd, name, child_fd in links:
        named = os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        opened = os.fstat(child_fd)

        if (
            not stat.S_ISDIR(named.st_mode)
            or _identity(named) != _identity(opened)
        ):
            raise FileReplaceRejected("file_replace_directory_changed")


def _verify_original(
    parent_fd: int,
    filename: str,
    source_fd: int,
    original: os.stat_result,
    baseline_sha256: str,
) -> None:
    """同时核对目录项、打开的对象、读取期间变化和字节摘要。"""

    named = os.stat(
        filename,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    opened = os.fstat(source_fd)

    if (
        not stat.S_ISREG(named.st_mode)
        or named.st_nlink != 1
        or _file_version(named) != _file_version(original)
        or _file_version(opened) != _file_version(original)
    ):
        raise FileReplaceRejected("file_replace_target_changed")

    os.lseek(source_fd, 0, os.SEEK_SET)
    data = _read_bounded_bytes(source_fd)
    after = os.fstat(source_fd)

    if (
        _file_version(after) != _file_version(original)
        or len(data) != after.st_size
    ):
        raise FileReplaceRejected("file_replace_target_changed")

    # 原文也遵守项目的UTF-8文本契约，保留BOM和原始换行。
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileReplaceRejected(
            "file_replace_source_invalid",
        ) from None

    if b"\x00" in data:
        raise FileReplaceRejected("file_replace_source_invalid")

    if sha256(data).hexdigest() != baseline_sha256:
        raise FileReplaceRejected("file_replace_baseline_changed")


def replace_workspace_text_file(
    *,
    bound_root: str,
    relative_path: str,
    baseline_sha256: str,
    proposed_content: str,
    proposed_sha256: str,
    expected_parent_identity: tuple[int, int] | None = None,
) -> FileReplaceResult:
    """替换现有文件；调用者必须自行完成授权与单次执行占用。

    本函数没有数据库事务，也不接受模型直接指定的执行目标。
    成功结果只描述本次操作，不能承诺返回后文件不会再次变化。
    """

    status: ReplaceStatus = "not_replaced"
    code = "file_replace_failed"
    cleanup_complete = True

    # 一旦开始调用replace，之后的异常都保守归为结果未确认。
    replace_attempted = False
    temp_name: str | None = None
    temp_fd: int | None = None
    parent_fd: int | None = None

    def close_descriptor(descriptor: int) -> None:
        nonlocal cleanup_complete

        try:
            os.close(descriptor)
        except OSError:
            # 不盲目重试close，避免描述符编号被复用后关闭其他资源。
            cleanup_complete = False

    with ExitStack() as stack:
        try:
            _require_supported_platform()

            if (
                not isinstance(bound_root, str)
                or not bound_root
                or "\x00" in bound_root
                or not isinstance(baseline_sha256, str)
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    baseline_sha256,
                ) is None
            ):
                raise FileReplaceRejected("file_replace_input_invalid")

            root = Path(bound_root)
            relative = _parse_relative_path(relative_path)

            if (
                not root.is_absolute()
                or root == Path(root.anchor)
                or ".." in root.parts
                or str(root) != bound_root
                or not relative.parts
                or len(bound_root) > 4096
                or len(relative_path) > 4096
            ):
                raise FileReplaceRejected("file_replace_input_invalid")

            content = _validate_content(
                proposed_content,
                proposed_sha256,
            )

            directory_flags = (
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
            )

            parent_fd = os.open(root.anchor, directory_flags)
            stack.callback(close_descriptor, parent_fd)

            links: list[tuple[int, str, int]] = []

            # 从文件系统根逐级打开，拒绝根目录祖先和项目内的链接。
            # 不先resolve再普通open，避免悄悄跟随变化后的链接。
            components = root.parts[1:] + relative.parts[:-1]

            for component in components:
                child_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=parent_fd,
                )
                stack.callback(close_descriptor, child_fd)
                links.append((parent_fd, component, child_fd))
                parent_fd = child_fd

            # 样例门禁提供实际父目录身份；打开后、访问文件前再次核对。
            if expected_parent_identity is not None and _identity(os.fstat(parent_fd)) != expected_parent_identity:
                raise FileReplaceRejected("file_replace_directory_changed")

            filename = relative.name
            original = os.stat(
                filename,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )

            # 本阶段仅处理当前用户拥有且具有owner写权限的普通文件。
            # 多硬链接会使“替换路径”和“修改共享对象”的语义不同，拒绝。
            if (
                not stat.S_ISREG(original.st_mode)
                or original.st_nlink != 1
                or original.st_uid != os.geteuid()
                or not original.st_mode & stat.S_IWUSR
                or original.st_mode
                & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
            ):
                raise FileReplaceRejected(
                    "file_replace_target_unsupported",
                )

            source_fd = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_fd,
            )
            stack.callback(close_descriptor, source_fd)

            _verify_directory_chain(links)
            _verify_original(
                parent_fd,
                filename,
                source_fd,
                original,
                baseline_sha256,
            )

            # 创建临时文件前先确认原文件元数据属于支持范围。
            metadata = read_file_metadata(source_fd)

            # 元数据读取期间也可能发生正文或文件身份变化。
            _verify_original(
                parent_fd,
                filename,
                source_fd,
                original,
                baseline_sha256,
            )

            # 使用同目录独占创建，避免覆盖已有临时文件。
            # 只有创建成功才记录temp_name，失败时不会清理他人的文件。
            candidate = f".agent-edit-{uuid4().hex}.tmp"
            temp_fd = os.open(
                candidate,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            temp_name = candidate
            stack.callback(close_descriptor, temp_fd)

            offset = 0
            while offset < len(content):
                written = os.write(temp_fd, content[offset:])
                if written <= 0:
                    raise FileReplaceRejected(
                        "file_replace_staging_failed",
                    )
                offset += written

            # 正文写完后复制权限与 ACL，避免后续写入改变权限状态。
            # 此时仍未越过 replace 提交边界，失败只清理临时文件。
            copy_file_metadata(
                source_fd,
                temp_fd,
                metadata,
            )
            os.fsync(temp_fd)

            # 重新读临时文件确认完整内容；不能只依赖write返回次数。
            os.lseek(temp_fd, 0, os.SEEK_SET)
            if _read_bounded_bytes(temp_fd) != content:
                raise FileReplaceRejected(
                    "file_replace_staging_failed",
                )

            _verify_original(
                parent_fd,
                filename,
                source_fd,
                original,
                baseline_sha256,
            )
            verify_file_metadata(source_fd, metadata)
            verify_file_metadata(temp_fd, metadata)
            _verify_directory_chain(links)

            named_temp = os.stat(
                temp_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if _identity(named_temp) != _identity(os.fstat(temp_fd)):
                raise FileReplaceRejected(
                    "file_replace_staging_changed",
                )

            # 文件系统提交边界：此处没有数据库事务。
            # 最后检查与replace之间仍存在竞态，不是文件系统CAS。
            replace_attempted = True
            os.replace(
                temp_name,
                filename,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            temp_name = None

            # 替换后同步目录项，再核对目标仍指向我们写好的对象。
            # 任一步失败都不能退回not_replaced。
            os.fsync(parent_fd)
            _verify_directory_chain(links)

            named_target = os.stat(
                filename,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(named_target.st_mode)
                or _identity(named_target)
                != _identity(os.fstat(temp_fd))
            ):
                raise FileReplaceRejected(
                    "file_replace_target_changed",
                )

            os.lseek(temp_fd, 0, os.SEEK_SET)
            if _read_bounded_bytes(temp_fd) != content:
                raise FileReplaceRejected(
                    "file_replace_target_changed",
                )

            # 已经尝试替换，元数据核对失败必须进入uncertain。
            verify_file_metadata(temp_fd, metadata)

            status = "replaced"
            code = "file_replaced"

        except (FileReplaceRejected, FileMetadataError) as error:
            status = "uncertain" if replace_attempted else "not_replaced"
            code = (
                "file_replace_uncertain"
                if replace_attempted
                else error.code
            )

        except (WorkspacePathError, WorkspaceFileError):
            status = "uncertain" if replace_attempted else "not_replaced"
            code = (
                "file_replace_uncertain"
                if replace_attempted
                else "file_replace_target_invalid"
            )

        except Exception:  # noqa: BLE001 -- 替换尝试后的未知异常必须保留uncertain语义
            # 不将底层OSError、路径或正文直接带出内部服务。
            status = "uncertain" if replace_attempted else "not_replaced"
            code = (
                "file_replace_uncertain"
                if replace_attempted
                else "file_replace_failed"
            )

        finally:
            # 描述符尚未关闭，清理相对于原父目录执行。
            # 不回滚目标文件；替换后盲目回滚可能覆盖外部编辑。
            if (
                temp_name is not None
                and temp_fd is not None
                and parent_fd is not None
            ):
                try:
                    named = os.stat(
                        temp_name,
                        dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    if _identity(named) == _identity(os.fstat(temp_fd)):
                        os.unlink(temp_name, dir_fd=parent_fd)
                    else:
                        cleanup_complete = False
                except FileNotFoundError:
                    pass
                except OSError:
                    cleanup_complete = False

    return FileReplaceResult(
        status=status,
        code=code,
        cleanup_complete=cleanup_complete,
    )
