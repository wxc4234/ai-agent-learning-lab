"""进程内Task专属Git样例；不绑定Workspace.root_path或开放普通目录。"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import RLock

from app.database import SessionLocal
from app.services.tasks.task_workspace import owned_task
from app.services.workspace.git.status_capture import (
    GitStatusSample, collect_sample_git_status, temporary_git_status_sample,
)
from app.services.workspace.git.status_parser import GitStatusSnapshot


class TaskGitSampleError(ValueError):
    """登记缺失、忙碌或封锁统一拒绝，不附带私有目录。"""

    def __init__(self):
        super().__init__('当前任务的Git样例不可用')
        self.code = 'task_git_sample_unavailable'


@dataclass
class _Binding:
    identities: tuple[int, int, int]
    lifetime: AbstractContextManager[GitStatusSample]
    sample: GitStatusSample
    busy: bool = False
    sealed: bool = False


def _authorize(key: tuple[int, str, str]) -> tuple[int, int, int]:
    # 仅只读事务；返回普通身份快照，Git初始化/采集均在Session关闭后进行。
    with SessionLocal() as session:
        task, conversation = owned_task(session, *key)
        return task.workspace_id, task.id, conversation.id


class TaskGitSamples:
    """由可信服务端持有；重启/新实例不恢复旧句柄或执行权。"""

    def __init__(self):
        self._lock = RLock()
        self._bindings: dict[tuple[int, str, str], _Binding] = {}
        self._closed = False

    def bind(self, *, user_id: int, workspace_id: str, task_id: str) -> None:
        key = (user_id, workspace_id, task_id)
        with self._lock:
            if self._closed or key in self._bindings:
                raise TaskGitSampleError()
            identities = _authorize(key)
            lifetime = temporary_git_status_sample()
            sample = lifetime.__enter__()
            try:
                # 初始化期间资源可能变化，再核对归属及内部身份才公布登记。
                if _authorize(key) != identities:
                    raise TaskGitSampleError()
            except BaseException:
                lifetime.__exit__(None, None, None)
                raise
            self._bindings[key] = _Binding(identities, lifetime, sample)

    def read_status(self, *, user_id: int, workspace_id: str, task_id: str) -> GitStatusSnapshot:
        key = (user_id, workspace_id, task_id)
        with self._lock:
            binding = self._bindings.get(key)
            if self._closed or binding is None or binding.busy or binding.sealed:
                raise TaskGitSampleError()
            # 标记跨越授权、进程采集和异常收尾，关闭不能越过这个借用边界。
            binding.busy = True
        try:
            if _authorize(key) != binding.identities:
                raise TaskGitSampleError()
            return collect_sample_git_status(binding.sample)
        finally:
            with self._lock:
                binding.busy = False

    def close(self, *, user_id: int, workspace_id: str, task_id: str) -> None:
        key = (user_id, workspace_id, task_id)
        with self._lock:
            binding = self._bindings.get(key)
            if self._closed or binding is None or binding.busy or binding.sealed:
                raise TaskGitSampleError()
            if _authorize(key) != binding.identities:
                raise TaskGitSampleError()
            self._close(key, binding)

    def _close(self, key, binding):
        # 清理失败不自动重试或重新借用；保留登记供可信宿主诊断。
        binding.sealed = True
        binding.lifetime.__exit__(None, None, None)
        del self._bindings[key]

    def stop_accepting(self) -> None:
        """宿主停止时封闭新操作；在途借用仍由原调用者释放。"""
        with self._lock:
            self._closed = True

    def shutdown(self) -> None:
        """可信宿主停止服务时释放自有样例；即使任务已删除也能收尾。

        不作为HTTP/模型能力，不通过失效的数据库身份推导外部清理权限。
        """
        with self._lock:
            if any(binding.busy or binding.sealed for binding in self._bindings.values()):
                raise TaskGitSampleError()
            self._closed = True
            for key, binding in list(self._bindings.items()):
                self._close(key, binding)
