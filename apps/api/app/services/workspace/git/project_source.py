"""只读观察Task绑定根目录；不是Git仓库检查、内容快照或执行许可。"""

import os
import stat
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TypeVar

from app.database import SessionLocal
from app.repositories.workspace.project_git_repository import read_owned_git_binding
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git.project_status_plan import build_project_git_status_plan


class ProjectGitSourceError(ValueError):
    """固定错误码；底层目录/数据库异常不会进入公开消息。"""

    def __init__(self, code: str = 'project_git_source_unavailable') -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProjectGitSourceObservation:
    # 内部身份保留到后续复核；本对象不注册为可执行句柄，不能用于重新授权。
    user_id: int
    workspace_pk: int
    task_pk: int
    conversation_pk: int
    binding_revision: int
    root_identity: tuple[int, int]
    bound_root: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _OpenedRoot:
    identity: tuple[int, int]
    descriptor: int = field(repr=False)


@contextmanager
def _observe_root(bound_root: str) -> Generator[_OpenedRoot, None, None]:
    """逐段无跟随打开；描述符保留到数据库复核后，关闭前核对目录项身份。

    检查只能发现被观察到的替换，不能发现所有ABA或阻止返回后的外部修改。
    绑定表没有保存原始inode，因此也不能识别首次打开前已经完成的同路径替换。
    不读取.git、目录内容或文件正文，也不声称已取得内容快照。
    """
    if (os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')
            or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd
            or os.stat not in os.supports_follow_symlinks):
        raise ProjectGitSourceError('project_git_source_platform_unsupported')
    root = PurePosixPath(bound_root)
    if (len(bound_root) > 4096 or not root.is_absolute() or root == PurePosixPath('/')
            or bound_root.startswith('//') or str(root) != bound_root or '..' in root.parts
            or len(root.parts) > 128 or '\\' in bound_root
            or any(ord(char) < 32 or ord(char) == 127 for char in bound_root)):
        raise ProjectGitSourceError()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        parent = os.open('/', flags)
        stack.callback(os.close, parent)
        links: list[tuple[int, str, int]] = []
        for part in root.parts[1:]:
            child = os.open(part, flags, dir_fd=parent)
            stack.callback(os.close, child)
            links.append((parent, part, child))
            parent = child

        def verify() -> None:
            for directory, name, opened in links:
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                current = os.fstat(opened)
                if not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (current.st_dev, current.st_ino):
                    raise ProjectGitSourceError('project_git_source_changed')

        verify()
        info = os.fstat(parent)
        yield _OpenedRoot((info.st_dev, info.st_ino), parent)
        verify()


T = TypeVar('T')


def _read_with_inspection(
    request: object, inspect: Callable[[int], AbstractContextManager[T]],
) -> tuple[ProjectGitSourceObservation, T]:
    """仅接受服务端固定观察器；观察器覆盖第二次授权，关闭后才返回结果。"""
    reference = build_project_git_status_plan(request).request
    try:
        def read_binding() -> dict:
            with SessionLocal() as session:
                return dict(read_owned_git_binding(session, user_id=reference.user_id,
                    workspace_id=reference.workspace_id, task_id=reference.task_id))

        source = read_binding()
        if source['root_path'] is None:
            raise ProjectGitSourceError('project_git_source_unbound')
        if source['binding_revision'] != reference.binding_revision:
            raise ProjectGitSourceError('project_git_source_stale')
        # 第一段只读事务已关闭；所有目录描述符在末次查询及目录复核后释放。
        with _observe_root(source['root_path']) as opened, inspect(opened.descriptor) as inspected:
            latest = read_binding()
            if latest != source:
                raise ProjectGitSourceError('project_git_source_changed')
            result = ProjectGitSourceObservation(
                user_id=reference.user_id, workspace_pk=source['workspace_pk'], task_pk=source['task_pk'],
                conversation_pk=source['conversation_pk'], binding_revision=source['binding_revision'],
                root_identity=opened.identity, bound_root=source['root_path'],
            )
        return result, inspected
    except (WorkspaceNotAccessibleError, ProjectGitSourceError):
        raise
    except Exception:  # noqa: BLE001 -- 数据库/OS未知失败不返回部分观察，中断继续传播。
        raise ProjectGitSourceError() from None


def read_project_git_source(request: object) -> ProjectGitSourceObservation:
    """只观察根目录；不会因为增加布局观察器而隐式开始扫描.git。"""
    result, _ = _read_with_inspection(request, lambda descriptor: nullcontext(None))
    return result
