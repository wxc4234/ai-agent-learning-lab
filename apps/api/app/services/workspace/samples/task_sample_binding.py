"""内部样例绑定：用户任务授权与进程内来源登记，不提供HTTP执行入口。"""

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal, cast

from app.config import settings
from app.database import SessionLocal
from app.models import WorkspaceSampleOrigin
from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task
from app.repositories.workspace.proposal_application_guard import require_no_active_proposal_application
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.samples.cleanup_preflight import (
    TaskSampleCleanupPreflight,
    inspect_pending_sample_directory,
)
from app.services.workspace.samples.temporary_proposal_sample import TemporaryProposalSample
from app.services.workspace.samples.temporary_sample_registry import SampleHandle, TemporarySampleRegistry


class TaskSampleBindingError(ValueError):
    def __init__(self, code: str = 'task_sample_unavailable') -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TaskSampleStatus:
    """仅公开登记状态与脱敏封锁原因；不证明文件现状或预留执行权。"""

    status: Literal['missing', 'busy', 'sealed', 'ready']
    sealed_reason: Literal['cleanup_pending', 'unavailable'] | None


@dataclass(repr=False)
class _Binding:
    handle: SampleHandle
    root: str = field(repr=False)
    state: str = 'preparing'
    busy: bool = False


class TaskSampleBindings:
    """由进程长生命周期的可信调用方持有，不允许客户端注入登记或路径。

    身份来自可信服务端调用方。借用授权只是当次快照，执行器仍须重新授权；
    持久来源只用于诊断，不恢复进程内句柄。不持有数据库事务跨越文件I/O；
    提交未确认或使用异常后保留现场，不自动恢复。
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
        """重新授权后的登记快照；持久来源只用于失联诊断，不恢复执行。"""
        self._check()
        key = (user_id, workspace_id, task_id)
        # 与bind/close保持相同锁顺序：进程锁→数据库归属锁，避免反向等待。
        # 即使不存在登记也先授权，不能让未知资源被解释成合法missing。
        # 使用独立短事务；Session退出释放锁，不提交或改变数据库状态。
        with self._lock, SessionLocal() as session:
            workspace, task = self._authorize(session, *key)
            binding = self._bindings.get(key)
            # 即使内存登记已丢失，也要读取来源；不能把遗留样例误报成普通missing。
            origin = session.get(WorkspaceSampleOrigin, workspace.id)
            if binding is None:
                # 有来源但无可信进程句柄，包含来源/目录错配，均保守报告sealed。
                status = 'sealed' if origin is not None else 'missing'
            else:
                if (
                    workspace.root_path != binding.root
                    or origin is None
                    or origin.task_id != task.id
                    or origin.root_path != binding.root
                    or origin.lifecycle_state != 'active'
                ):
                    # 只报告当前不再匹配，不在查询中封锁或修复原登记。
                    status = 'sealed'
                elif binding.state not in ('ready', 'preparing'):
                    status = 'sealed'
                elif binding.busy or binding.state == 'preparing':
                    status = 'busy'
                else:
                    status = 'ready'
            # 只有来源Task能获知持久清理待办；其他已授权Task只看到一般封锁。
            # 原因来自数据库快照，不据此推断目录是否仍在或允许重新执行。
            sealed_reason = (
                'cleanup_pending'
                if status == 'sealed' and origin is not None
                and origin.task_id == task.id and origin.lifecycle_state == 'cleanup_pending'
                else 'unavailable' if status == 'sealed' else None
            )
        # 离开事务和进程锁后返回；之后可能立即变化，执行仍需重新借用授权。
        return TaskSampleStatus(status=status, sealed_reason=sealed_reason)

    def read_cleanup_preflight(
        self, *, user_id: int, workspace_id: str, task_id: str,
    ) -> TaskSampleCleanupPreflight:
        """重新授权后只读比较待办与当前候选；结果不授予清理权限。"""
        self._check()
        key = (user_id, workspace_id, task_id)
        # 与 bind/close 同锁序；只复制证据，事务结束后才访问文件系统。
        with self._lock, SessionLocal() as session:
            workspace, task = self._authorize(session, *key)
            origin = session.get(WorkspaceSampleOrigin, workspace.id)
            if origin is None:
                return TaskSampleCleanupPreflight('evidence_missing')
            if origin.task_id != task.id:
                # 同工作空间的其他 Task 也不能获知来源诊断；与未知资源统一掩蔽。
                raise WorkspaceNotAccessibleError()

            identities = (
                origin.parent_dev,
                origin.parent_ino,
                origin.root_dev,
                origin.root_ino,
            )
            if all(value is None for value in identities):
                expected_parent_identity = None
                expected_root_identity = None
            elif all(type(value) is int for value in identities):
                # 运行时已逐项验证；显式收窄供静态类型检查器识别。
                parent_dev, parent_ino, root_dev, root_ino = cast(
                    tuple[int, int, int, int], identities,
                )
                expected_parent_identity = (parent_dev, parent_ino)
                expected_root_identity = (root_dev, root_ino)
            else:
                return TaskSampleCleanupPreflight('evidence_inconsistent')

            if origin.lifecycle_state == 'active' and workspace.root_path == origin.root_path:
                return TaskSampleCleanupPreflight('not_pending')
            if origin.lifecycle_state != 'cleanup_pending' or workspace.root_path is not None:
                return TaskSampleCleanupPreflight('evidence_inconsistent')
            root_path = origin.root_path

        # 返回固定分类，不返回路径、身份数值或可供后续清理使用的句柄。
        result = inspect_pending_sample_directory(
            root_path,
            expected_parent_identity=expected_parent_identity,
            expected_root_identity=expected_root_identity,
        )
        return TaskSampleCleanupPreflight(result)

    def bind(self, *, user_id: int, workspace_id: str, task_id: str) -> None:
        self._check()
        key = (user_id, workspace_id, task_id)
        with self._lock:
            if key in self._bindings:
                raise TaskSampleBindingError()
            # 先授权再创建文件；关闭只读事务，不持锁等待文件创建。
            with SessionLocal() as session:
                workspace, _ = self._authorize(session, *key)
                if workspace.root_path is not None or session.get(WorkspaceSampleOrigin, workspace.id) is not None:
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
                        workspace, task = self._authorize(session, *key)
                        if workspace.root_path is not None or session.get(WorkspaceSampleOrigin, workspace.id) is not None:
                            raise TaskSampleBindingError('task_sample_already_bound')
                        require_no_active_proposal_application(session, workspace_id=workspace.id)
                        # 身份取自本次借用的已核验样例；绑定、来源及身份同事务提交。
                        parent_dev, parent_ino = sample.parent_identity
                        root_dev, root_ino = sample.root_identity
                        workspace.root_path = binding.root
                        session.add(WorkspaceSampleOrigin(
                            workspace_id=workspace.id,
                            task_id=task.id,
                            root_path=binding.root,
                            parent_dev=parent_dev,
                            parent_ino=parent_ino,
                            root_dev=root_dev,
                            root_ino=root_ino,
                            lifecycle_state='active',
                        ))
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
                        workspace, task = self._authorize(session, *key)
                        origin = session.get(WorkspaceSampleOrigin, workspace.id)
                        if (
                            workspace.root_path != binding.root
                            or str(sample.root) != binding.root
                            or origin is None
                            or origin.task_id != task.id
                            or origin.root_path != binding.root
                            or origin.lifecycle_state != 'active'
                        ):
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
        """先解绑并保留待办，确认文件清理后再撤销来源记录。"""
        self._check()
        key = (user_id, workspace_id, task_id)
        with self._lock:
            binding = self._bindings.get(key)
            if binding is None or binding.state != 'ready' or binding.busy:
                raise TaskSampleBindingError()
            attempted = False
            try:
                with SessionLocal() as session:
                    workspace, task = self._authorize(session, *key)
                    origin = session.get(WorkspaceSampleOrigin, workspace.id)
                    if (
                        workspace.root_path != binding.root
                        or origin is None
                        or origin.task_id != task.id
                        or origin.root_path != binding.root
                        or origin.lifecycle_state != 'active'
                    ):
                        raise TaskSampleBindingError()
                    require_no_active_proposal_application(session, workspace_id=workspace.id)
                    # 同事务解绑并保留来源待办；提交未确认时绝不猜测能否清理。
                    origin.lifecycle_state = 'cleanup_pending'
                    workspace.root_path = None
                    session.flush()
                    attempted = True
                    session.commit()
            except BaseException:
                if attempted:
                    binding.state = 'uncertain'
                raise
            # 解绑已确认，但待办仍在；清理失败保留来源并封锁进程登记。
            binding.state = 'uncertain'
            if not self._registry.close(binding.handle):
                raise TaskSampleBindingError('sample_cleanup_incomplete')

            # 文件安全清理确认后，才用第二次短事务撤销持久待办。
            # 最终提交未确认时仍保留封锁登记，不猜测数据库是否已删除记录。
            with SessionLocal() as session:
                workspace, task = self._authorize(session, *key)
                origin = session.get(WorkspaceSampleOrigin, workspace.id)
                if (
                    workspace.root_path is not None
                    or origin is None
                    or origin.task_id != task.id
                    or origin.root_path != binding.root
                    or origin.lifecycle_state != 'cleanup_pending'
                ):
                    raise TaskSampleBindingError()
                session.delete(origin)
                session.flush()
                session.commit()
            del self._bindings[key]
