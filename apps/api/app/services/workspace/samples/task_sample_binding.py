"""内部样例绑定：用户任务授权与进程内来源登记，不提供HTTP执行入口。"""

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

from app.config import settings
from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task
from app.repositories.workspace.proposal_application_guard import require_no_active_proposal_application
from app.services.workspace.samples.temporary_proposal_sample import TemporaryProposalSample
from app.services.workspace.samples.temporary_sample_registry import SampleHandle, TemporarySampleRegistry


class TaskSampleBindingError(ValueError):
    def __init__(self, code: str = 'task_sample_unavailable') -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TaskSampleStatus:
    """仅公开登记状态；ready不证明文件仍有效，也不预留执行权。"""

    status: Literal['missing', 'busy', 'sealed', 'ready']


@dataclass(repr=False)
class _Binding:
    handle: SampleHandle
    root: str = field(repr=False)
    state: str = 'preparing'
    busy: bool = False


class TaskSampleBindings:
    """由进程长生命周期的可信调用方持有，不允许客户端注入登记或路径。

    身份来自可信服务端调用方。借用授权只是当次快照，执行器仍须重新授权；
    不持有数据库事务跨越文件I/O。提交未确认或使用异常后保留现场，不自动恢复。
    """

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._lock = RLock()
        self._registry = TemporarySampleRegistry()
        self._bindings: dict[tuple[int, str, str], _Binding] = {}

    def _check(self) -> None:
        if self._pid != os.getpid() or settings.app_mode != 'local':
            raise TaskSampleBindingError()

    @staticmethod
    def _authorize(session, user_id, workspace_id, task_id):
        workspace, task = lock_owned_proposal_task(
            session, user_id=user_id, workspace_id=workspace_id, task_id=task_id,
        )
        return workspace, task

    def read_status(self, *, user_id: int, workspace_id: str, task_id: str) -> TaskSampleStatus:
        """重新授权后的进程内快照，不创建/借用/恢复登记，不访问文件。"""
        self._check()
        key = (user_id, workspace_id, task_id)
        # 与bind/close保持相同锁顺序：进程锁→数据库归属锁，避免反向等待。
        # 即使不存在登记也先授权，不能让未知资源被解释成合法missing。
        # 使用独立短事务；Session退出释放锁，不提交或改变数据库状态。
        with self._lock, SessionLocal() as session:
            workspace, _ = self._authorize(session, *key)
            binding = self._bindings.get(key)
            if binding is None:
                status = 'missing'
            elif workspace.root_path != binding.root:
                # 只报告当前不再匹配，不在查询中封锁或修复原登记。
                status = 'sealed'
            elif binding.state not in ('ready', 'preparing'):
                status = 'sealed'
            elif binding.busy or binding.state == 'preparing':
                status = 'busy'
            else:
                status = 'ready'
        # 离开事务和进程锁后返回；之后可能立即变化，执行仍需重新借用授权。
        return TaskSampleStatus(status=status)

    def bind(self, *, user_id: int, workspace_id: str, task_id: str) -> None:
        self._check()
        key = (user_id, workspace_id, task_id)
        with self._lock:
            if key in self._bindings:
                raise TaskSampleBindingError()
            # 先授权再创建文件；关闭只读事务，不持锁等待文件创建。
            with SessionLocal() as session:
                workspace, _ = self._authorize(session, *key)
                if workspace.root_path is not None:
                    raise TaskSampleBindingError('task_sample_already_bound')
                require_no_active_proposal_application(session, workspace_id=workspace.id)
            handle = self._registry.create()
            attempted = False
            failure = None
            binding = None
            with self._registry.borrow(handle) as sample:
                # 在借用内接住数据库失败，防止底层按“使用异常”提前删除目录。
                try:
                    binding = _Binding(handle, str(sample.root))
                    self._bindings[key] = binding
                    with SessionLocal() as session:
                        workspace, _ = self._authorize(session, *key)
                        if workspace.root_path is not None:
                            raise TaskSampleBindingError('task_sample_already_bound')
                        require_no_active_proposal_application(session, workspace_id=workspace.id)
                        workspace.root_path = binding.root
                        session.flush()
                        # flush失败有未提交证据；开始commit之后的失败一律未确认。
                        attempted = True
                        session.commit()
                    binding.state = 'ready'
                except BaseException as error:  # noqa: BLE001 -- 延迟传播中断，先保留数据库引用的目录
                    failure = error
                    if binding is not None:
                        binding.state = 'uncertain'
            if failure is not None:
                if not attempted:
                    try:
                        self._registry.close(handle)
                    except BaseException:  # noqa: BLE001 -- 保留原异常并报告清理失败
                        failure.add_note('sample_cleanup_incomplete')
                    else:
                        self._bindings.pop(key, None)
                # 未确认的提交保留目录与封锁登记，不补提交、不自动解绑。
                raise failure

    @contextmanager
    def borrow(self, *, user_id: int, workspace_id: str, task_id: str) -> Generator[TemporaryProposalSample, None, None]:
        self._check()
        key = (user_id, workspace_id, task_id)
        with self._lock:
            binding = self._bindings.get(key)
            if binding is None or binding.state != 'ready' or binding.busy:
                raise TaskSampleBindingError()
            binding.busy = True
        failure = None
        try:
            with self._registry.borrow(binding.handle) as sample:
                try:
                    with SessionLocal() as session:
                        workspace, _ = self._authorize(session, *key)
                        if workspace.root_path != binding.root or str(sample.root) != binding.root:
                            raise TaskSampleBindingError()
                    # 无活动数据库事务；调用方不能让后台任务跨越此作用域。
                    yield sample
                except BaseException as error:  # noqa: BLE001 -- 延迟传播中断，先保留数据库引用的目录
                    failure = error
                    binding.state = 'uncertain'
            if failure is not None:
                raise failure
        except BaseException:
            binding.state = 'uncertain'
            raise
        finally:
            with self._lock:
                binding.busy = False

    def close(self, *, user_id: int, workspace_id: str, task_id: str) -> None:
        """只有已授权、无活动使用及应用占用时，先确认解绑提交，再删除目录。"""
        self._check()
        key = (user_id, workspace_id, task_id)
        with self._lock:
            binding = self._bindings.get(key)
            if binding is None or binding.state != 'ready' or binding.busy:
                raise TaskSampleBindingError()
            attempted = False
            try:
                with SessionLocal() as session:
                    workspace, _ = self._authorize(session, *key)
                    if workspace.root_path != binding.root:
                        raise TaskSampleBindingError()
                    require_no_active_proposal_application(session, workspace_id=workspace.id)
                    workspace.root_path = None
                    session.flush()
                    attempted = True
                    session.commit()
            except BaseException:
                if attempted:
                    binding.state = 'uncertain'
                raise
            # 解绑已提交；清理失败仍封锁，不把残留目录重新发布。
            binding.state = 'uncertain'
            self._registry.close(binding.handle)
            del self._bindings[key]
