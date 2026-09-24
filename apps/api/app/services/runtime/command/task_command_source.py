"""Task 样例的内部命令来源借用；不执行命令，也不接受宿主路径。"""

import os
import stat
from collections.abc import Generator
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from app.services.runtime.agent.tool_execution_context import load_tool_execution_context
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.services.workspace.files.workspace_file import (
    _file_version,
    _read_bounded_bytes,
    _require_supported_file_access,
)
from app.services.workspace.samples.temporary_proposal_sample import (
    SAMPLE_FILENAME,
    TemporaryProposalSample,
)
from app.tools.context import ToolExecutionContext


class TaskCommandSourceUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__('task_command_source_unavailable')


@dataclass(slots=True)
class _Lifetime:
    pid: int = field(default_factory=os.getpid)
    active: bool = True


@dataclass(frozen=True, slots=True, repr=False)
class TaskCommandSource:
    """仅在当前借用内有效；读取结果不包含宿主路径或文件描述符。"""

    context: ToolExecutionContext
    _sample: TemporaryProposalSample
    _lifetime: _Lifetime

    @property
    def root(self) -> Path:
        # 无法撤销此前复制出的 Path，因此调用方仍须遵守借用作用域。
        if not self._lifetime.active or self._lifetime.pid != os.getpid():
            raise TaskCommandSourceUnavailable()
        return self._sample.root

    def read_sample_bytes(self) -> bytes:
        """只读取固定 example.txt，保留原始字节，不进行文本归一化。"""

        root = self.root
        _require_supported_file_access()

        def identity(info: os.stat_result) -> tuple[int, int]:
            return info.st_dev, info.st_ino

        def check_root(parent_fd: int, root_fd: int) -> None:
            # 同时核对持有的描述符与当前目录项，拒绝常见的目录替换。
            parent = os.fstat(parent_fd)
            opened = os.fstat(root_fd)
            linked = os.stat(
                root.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            visible_parent = os.stat(root.parent, follow_symlinks=False)

            if (
                identity(parent) != self._sample.parent_identity
                or identity(visible_parent) != self._sample.parent_identity
                or not stat.S_ISDIR(visible_parent.st_mode)
                or identity(opened) != self._sample.root_identity
                or identity(linked) != self._sample.root_identity
                or not stat.S_ISDIR(linked.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o700
            ):
                raise TaskCommandSourceUnavailable()

        def check_file(info: os.stat_result) -> None:
            # 固定样例只接受本用户拥有的单链接普通文件。
            # 不要求固定 inode，因为提案应用可以合法地原子替换文件。
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise TaskCommandSourceUnavailable()

        try:
            if not root.is_absolute() or ".." in root.parts:
                raise TaskCommandSourceUnavailable()

            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

            # 从根目录逐级打开，保护样例目录及其祖先路径。
            # ExitStack 在成功和异常路径都按逆序关闭描述符。
            with ExitStack() as stack:
                parent_fd = os.open(root.anchor, directory_flags)
                stack.callback(os.close, parent_fd)

                for component in root.parts[1:-1]:
                    parent_fd = os.open(
                        component,
                        directory_flags,
                        dir_fd=parent_fd,
                    )
                    stack.callback(os.close, parent_fd)

                # 先确认父目录，再访问样例根目录。
                if identity(os.fstat(parent_fd)) != self._sample.parent_identity:
                    raise TaskCommandSourceUnavailable()

                root_fd = os.open(
                    root.name,
                    directory_flags,
                    dir_fd=parent_fd,
                )
                stack.callback(os.close, root_fd)
                check_root(parent_fd, root_fd)

                before = os.stat(
                    SAMPLE_FILENAME,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
                check_file(before)

                # O_NONBLOCK 避免文件被替换成 FIFO 后阻塞在打开阶段。
                file_fd = os.open(
                    SAMPLE_FILENAME,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=root_fd,
                )
                stack.callback(os.close, file_fd)

                opened = os.fstat(file_fd)
                check_file(opened)
                if _file_version(opened) != _file_version(before):
                    raise TaskCommandSourceUnavailable()

                # 实际最多读取上限加一个字节，不能只相信 stat 的文件大小。
                content = _read_bounded_bytes(file_fd)

                after = os.fstat(file_fd)
                linked = os.stat(
                    SAMPLE_FILENAME,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
                check_file(after)
                check_file(linked)

                if (
                    _file_version(after) != _file_version(opened)
                    or _file_version(linked) != _file_version(opened)
                    or len(content) != after.st_size
                ):
                    raise TaskCommandSourceUnavailable()

                check_root(parent_fd, root_fd)

                # 返回前再确认借用仍有效；不把字节读取变成长期目录授权。
                if self.root != root:
                    raise TaskCommandSourceUnavailable()

                return content
        except OSError:
            raise TaskCommandSourceUnavailable() from None


@contextmanager
def borrow_task_command_source(
    *, user_id: int, conversation_id: str, bindings: TaskSampleBindings,
    expected_context: ToolExecutionContext | None = None,
) -> Generator[TaskCommandSource, None, None]:
    """user_id 来自服务端身份，conversation_id 来自当前请求，不取模型参数。

    同步授权/借用需在受管理的工作线程调用。调用者必须在退出前结束全部
    使用；未来 Docker 集成还需处理容器残留，不能靠退出作用域推断可释放。
    """

    context = load_tool_execution_context(user_id=user_id, conversation_id=conversation_id)
    if expected_context is not None and context != expected_context:
        raise TaskCommandSourceUnavailable()
    # 会话查询事务已关闭；借用自身重新授权 Task/Workspace、核对来源并独占。
    # 不先调用 read_status：ready 快照既不能预留，也不能替代真正的借用。
    with bindings.borrow(
        user_id=context.user_id, workspace_id=context.workspace_id, task_id=context.task_id,
    ) as sample:
        # 捕获首次查询与借用之间的可观察会话迁移；两次查询不是原子租约。
        current = load_tool_execution_context(user_id=user_id, conversation_id=conversation_id)
        if current != context:
            raise TaskCommandSourceUnavailable()
        lifetime = _Lifetime()
        try:
            # 无数据库事务跨越 yield；底层借用异常会封锁登记、保留持久来源。
            yield TaskCommandSource(context=context, _sample=sample, _lifetime=lifetime)
        finally:
            lifetime.active = False
