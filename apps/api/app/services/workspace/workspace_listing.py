"""单层、限量枚举已授权任务目录，不读取文件内容或跟随子项链接。"""

import errno
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from app.services.workspace.workspace_path import resolve_task_workspace_path


# 上限由服务端决定，暂不允许模型扩大扫描范围。
MAX_DIRECTORY_ENTRIES = 200

DirectoryEntryKind = Literal["file", "directory", "symlink", "other"]

WorkspaceListingErrorCode = Literal[
    "directory_listing_unsupported",
    "directory_listing_not_found",
    "directory_listing_not_directory",
    "directory_listing_access_denied",
    "directory_listing_changed",
    "directory_listing_unavailable",
]


class WorkspaceListingError(ValueError):
    """提供稳定分类，不返回底层异常中的绝对路径。"""

    def __init__(
        self,
        code: WorkspaceListingErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceDirectoryEntry:
    """只描述当前目录中的一个名称，不暴露链接目标或绝对路径。"""

    name: str
    kind: DirectoryEntryKind


@dataclass(frozen=True)
class WorkspaceDirectoryListing:
    """不可变的有限枚举结果；truncated 表示还有未返回的条目。"""

    relative_path: str
    entries: tuple[WorkspaceDirectoryEntry, ...]
    truncated: bool


def _require_supported_listing() -> None:
    """要求描述符枚举和逐级无跟随打开，能力不足时拒绝降级。"""

    if (
        os.name != "posix"
        or os.open not in os.supports_dir_fd
        or os.scandir not in os.supports_fd
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise WorkspaceListingError(
            "directory_listing_unsupported",
            "当前平台尚不支持受限目录枚举",
        )


def _entry_kind(mode: int) -> DirectoryEntryKind:
    """根据不跟随链接取得的元信息分类，不打开子项。"""

    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _directory_version(
    metadata: os.stat_result,
) -> tuple[int, int, int, int]:
    """用于发现常见目录变化，不作为原子快照或永久授权证明。"""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _scan_directory(
    descriptor: int,
) -> tuple[tuple[WorkspaceDirectoryEntry, ...], bool]:
    """有限枚举已打开目录，所有子项均不跟随符号链接。"""

    before = os.fstat(descriptor)
    entries: list[WorkspaceDirectoryEntry] = []
    truncated = False

    # scandir 迭代器必须关闭；调用方仍负责关闭传入的目录描述符。
    with os.scandir(descriptor) as iterator:
        for entry in iterator:
            # 额外取得一个条目用于确认截断，不继续扫描剩余目录。
            if len(entries) == MAX_DIRECTORY_ENTRIES:
                truncated = True
                break

            try:
                # 必须显式禁止跟随链接。
                # 断开的链接仍是链接，不需要目标存在。
                metadata = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                # 子项在枚举期间消失，不能悄悄跳过后声称结果完整。
                raise WorkspaceListingError(
                    "directory_listing_changed",
                    "目录内容在枚举期间发生变化，请重新查询",
                ) from None

            entries.append(
                WorkspaceDirectoryEntry(
                    name=entry.name,
                    kind=_entry_kind(metadata.st_mode),
                )
            )

    after = os.fstat(descriptor)

    if _directory_version(before) != _directory_version(after):
        raise WorkspaceListingError(
            "directory_listing_changed",
            "目录内容在枚举期间发生变化，请重新查询",
        )

    # 只排序已经取到的有限子集，不为全局排序扫描整个目录。
    # 使用名称的原始字符串顺序，不隐式去空格或改变大小写。
    entries.sort(key=lambda entry: entry.name)

    return tuple(entries), truncated


def _list_resolved_directory(
    path: Path,
) -> tuple[tuple[WorkspaceDirectoryEntry, ...], bool]:
    """逐级打开规范目录路径，拒绝解析之后新出现的链接。"""

    if not path.is_absolute() or ".." in path.parts:
        raise WorkspaceListingError(
            "directory_listing_unavailable",
            "目录路径无法用于受限枚举",
        )

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    # 每个打开成功的目录立即登记清理，包括中途失败的情况。
    # 不复用文件读取服务的私有函数，两个服务独立管理资源。
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

        return _scan_directory(descriptor)


def list_task_directory(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    relative_path: str = ".",
) -> WorkspaceDirectoryListing:
    """授权后枚举单层目录，返回有限条目和明确的截断标记。"""

    # 复用现有任务授权、绑定目录及路径边界。
    # 查询完成后 Session 已关闭，枚举不占用数据库事务。
    path = resolve_task_workspace_path(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        relative_path=relative_path,
    )

    _require_supported_listing()

    try:
        entries, truncated = _list_resolved_directory(path)

    except FileNotFoundError:
        raise WorkspaceListingError(
            "directory_listing_not_found",
            "目录已不存在",
        ) from None

    except NotADirectoryError:
        # 可能原本就是文件，也可能某段目录在打开前被替换。
        # 不把这种异常解释成确定的并发修改证据。
        raise WorkspaceListingError(
            "directory_listing_not_directory",
            "目标或路径中的某一项不是可打开的目录",
        ) from None

    except PermissionError:
        raise WorkspaceListingError(
            "directory_listing_access_denied",
            "没有权限枚举目标目录",
        ) from None

    except OSError as error:
        if error.errno == errno.ELOOP:
            raise WorkspaceListingError(
                "directory_listing_changed",
                "打开目录时遇到符号链接，请重新查询",
            ) from None

        raise WorkspaceListingError(
            "directory_listing_unavailable",
            "目标目录暂时无法枚举",
        ) from None

    return WorkspaceDirectoryListing(
        relative_path=PurePosixPath(relative_path).as_posix(),
        entries=entries,
        truncated=truncated,
    )
