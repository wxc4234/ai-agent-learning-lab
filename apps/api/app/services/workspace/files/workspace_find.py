"""在已授权项目目录内，有界递归查找文件名，不读取文件内容。"""

import errno
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from app.services.workspace.directory.workspace_path import resolve_task_workspace_path


# 限制由服务端固定，不能通过模型参数扩大扫描范围。
MAX_FIND_QUERY_CHARACTERS = 128
MAX_FIND_ENTRIES = 2_000
MAX_FIND_MATCHES = 50
MAX_FIND_DEPTH = 8
MAX_FIND_PATH_CHARACTERS = 4_096

WorkspaceFindErrorCode = Literal[
    "invalid_find_query",
    "file_find_unsupported",
    "file_find_changed",
    "file_find_access_denied",
    "file_find_unavailable",
]


class WorkspaceFindError(ValueError):
    """只提供稳定分类与安全文案，不反射宿主绝对路径。"""

    def __init__(
        self,
        code: WorkspaceFindErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceFindResult:
    """路径均相对于 Workspace 根目录，不返回宿主路径或句柄。"""

    relative_path: str
    query: str
    paths: tuple[str, ...]
    scanned_entries: int

    # 表示搜索未完整覆盖，不意味着一定还有匹配文件。
    truncated: bool


@dataclass
class _FindState:
    """一次调用独有的预算与结果，不与其他搜索共享。"""

    paths: list[str]
    scanned_entries: int = 0
    truncated: bool = False

    # 深度限制只跳过当前分支；全局预算耗尽才停止整个搜索。
    stopped: bool = False


def _validate_query(query: str) -> None:
    """只匹配单个文件名，不解释路径、通配符或正则。"""

    if (
        not isinstance(query, str)
        or not 1 <= len(query) <= MAX_FIND_QUERY_CHARACTERS
        or any(character in query for character in ("\x00", "\r", "\n"))
        or "/" in query
        or "\\" in query
    ):
        raise WorkspaceFindError(
            "invalid_find_query",
            "文件名查询须为1～128个字符，不能包含路径分隔符、换行或NUL",
        )


def _require_supported_find() -> None:
    """能力不足时拒绝，不退回会跟随链接的路径递归。"""

    if (
        os.name != "posix"
        or os.open not in os.supports_dir_fd
        or os.scandir not in os.supports_fd
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise WorkspaceFindError(
            "file_find_unsupported",
            "当前平台尚不支持受限文件查找",
        )


def _directory_version(
    metadata: os.stat_result,
) -> tuple[int, int, int, int]:
    """识别常见目录替换与修改，不承诺原子文件系统快照。"""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _changed() -> WorkspaceFindError:
    return WorkspaceFindError(
        "file_find_changed",
        "目录在查找期间发生变化，请重新查询",
    )


def _walk_directory(
    descriptor: int,
    *,
    relative_path: PurePosixPath,
    query: str,
    depth: int,
    state: _FindState,
) -> None:
    """沿已打开的目录描述符深度优先扫描，共享本次全局预算。"""

    before = os.fstat(descriptor)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    # 迭代器和子目录描述符由当前递归层负责关闭。
    # 最多保留固定深度的扫描栈，不预先加载整个目录。
    with os.scandir(descriptor) as iterator:
        for entry in iterator:
            # 额外取得一个条目，确认仍有未检查内容。
            # 不再读取该条目的元信息，也不继续枚举。
            if state.scanned_entries == MAX_FIND_ENTRIES:
                state.truncated = True
                state.stopped = True
                break

            state.scanned_entries += 1
            metadata = entry.stat(follow_symlinks=False)
            child_path = relative_path / entry.name
            child_text = child_path.as_posix()

            # 限制单条输出路径，也避免递归构造过长的路径字符串。
            if len(child_text) > MAX_FIND_PATH_CHARACTERS:
                state.truncated = True
                continue

            if stat.S_ISREG(metadata.st_mode):
                # 保留原始大小写和空格；星号等字符没有特殊含义。
                if query not in entry.name:
                    continue

                # 多发现一个匹配，才能确认返回量发生截断。
                if len(state.paths) == MAX_FIND_MATCHES:
                    state.truncated = True
                    state.stopped = True
                    break

                state.paths.append(child_text)

            elif stat.S_ISDIR(metadata.st_mode):
                # 起始目录为第0层，最多进入第8层子目录。
                # 未进入的目录可能包含匹配，因此必须标记不完整。
                if depth == MAX_FIND_DEPTH:
                    state.truncated = True
                    continue

                child_fd = os.open(
                    entry.name,
                    flags,
                    dir_fd=descriptor,
                )

                try:
                    opened = os.fstat(child_fd)

                    # 无跟随打开阻止链接替换；身份比较再发现目录替换。
                    if (
                        _directory_version(opened)
                        != _directory_version(metadata)
                    ):
                        raise _changed()

                    _walk_directory(
                        child_fd,
                        relative_path=child_path,
                        query=query,
                        depth=depth + 1,
                        state=state,
                    )
                finally:
                    os.close(child_fd)

                if state.stopped:
                    break

            # 符号链接、管道、设备等不属于本课的普通文件搜索范围。
            # 不打开、不跟随，也不读取链接目标。

    after = os.fstat(descriptor)

    if _directory_version(before) != _directory_version(after):
        raise _changed()


def _find_resolved_directory(
    path: Path,
    *,
    relative_path: PurePosixPath,
    query: str,
) -> _FindState:
    """逐级无跟随打开规范目录，再沿描述符递归。"""

    if not path.is_absolute() or ".." in path.parts:
        raise WorkspaceFindError(
            "file_find_unavailable",
            "目标目录无法用于受限查找",
        )

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    state = _FindState(paths=[])

    # 包括根目录祖先在内，所有成功打开的描述符都立即登记清理。
    # 任意阶段失败都会逆序关闭，不能把句柄留到下一次调用。
    with ExitStack() as stack:
        descriptor = os.open(path.anchor, flags)
        stack.callback(os.close, descriptor)

        for component in path.parts[1:]:
            descriptor = os.open(
                component,
                flags,
                dir_fd=descriptor,
            )
            stack.callback(os.close, descriptor)

        _walk_directory(
            descriptor,
            relative_path=relative_path,
            query=query,
            depth=0,
            state=state,
        )

    # 仅排序已经取得的有限结果，不为全局排序扫描整个项目。
    state.paths.sort()
    return state


def find_task_files(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    query: str,
    relative_path: str = ".",
) -> WorkspaceFindResult:
    """授权后查找文件，身份与任务定位必须来自服务端上下文。"""

    # 无效查询在任何数据库或文件系统访问前拒绝。
    _validate_query(query)

    # 复用任务归属与绑定目录校验。
    # 返回时查询事务已经结束，递归扫描不占用数据库 Session。
    path = resolve_task_workspace_path(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=relative_path,
    )

    _require_supported_find()
    parsed_path = PurePosixPath(relative_path)

    try:
        state = _find_resolved_directory(
            path,
            relative_path=parsed_path,
            query=query,
        )

    except FileNotFoundError:
        # 不静默跳过消失的子项，否则空结果可能被误认为完整搜索。
        raise _changed() from None

    except PermissionError:
        raise WorkspaceFindError(
            "file_find_access_denied",
            "没有权限查找目标目录",
        ) from None

    except OSError as error:
        if error.errno == errno.ELOOP:
            raise _changed() from None

        raise WorkspaceFindError(
            "file_find_unavailable",
            "目标或子目录暂时无法查找",
        ) from None

    return WorkspaceFindResult(
        relative_path=parsed_path.as_posix(),
        query=query,
        paths=tuple(state.paths),
        scanned_entries=state.scanned_entries,
        truncated=state.truncated,
    )
