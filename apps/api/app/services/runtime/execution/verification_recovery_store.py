"""应用持有验证恢复记录；单进程单事件循环，不自动淘汰或恢复执行权。"""

import os
from dataclasses import dataclass, field, replace

from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from app.services.runtime.verification.verification_journal import TaskVerificationJournal
from app.tools.context import ToolExecutionContext

MAX_VERIFICATION_RECOVERY_SCOPES = 32


@dataclass(frozen=True, slots=True)
class VerificationRecoveryScope:
    context: ToolExecutionContext
    run_id: int
    journal: TaskVerificationJournal = field(repr=False)


class VerificationRecoveryStore:
    """完整上下文与Run共同绑定；归属匹配只防内部误用，不替代数据库授权。

    所有记录保留至应用退出，满额拒绝新Run；不能靠删除失败记录腾出容量。
    """

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._closed = False
        self._scopes: dict[int, VerificationRecoveryScope] = {}

    def _check(self, context: ToolExecutionContext, run_id: int) -> None:
        if os.getpid() != self._pid or type(run_id) is not int or run_id <= 0:
            raise TaskSampleJournalUnavailable()
        # 复用完整上下文输入验证；临时对象不分配scope或执行记录。
        TaskVerificationJournal(context=context)

    def acquire(self, *, context: ToolExecutionContext, run_id: int) -> VerificationRecoveryScope:
        self._check(context, run_id)
        if self._closed:
            raise TaskSampleJournalUnavailable()
        scope = self._scopes.get(run_id)
        if scope is not None:
            scope.journal.require_context(context)
            return scope
        if len(self._scopes) >= MAX_VERIFICATION_RECOVERY_SCOPES:
            raise TaskSampleJournalUnavailable()
        scope = VerificationRecoveryScope(replace(context), run_id, TaskVerificationJournal(context=context))
        self._scopes[run_id] = scope
        return scope

    def get(self, *, context: ToolExecutionContext, run_id: int) -> VerificationRecoveryScope | None:
        self._check(context, run_id)
        scope = self._scopes.get(run_id)
        return scope if scope is not None and scope.context == context else None

    def close_scope(self, scope: VerificationRecoveryScope) -> None:
        if (os.getpid() != self._pid or type(scope) is not VerificationRecoveryScope
                or self._scopes.get(scope.run_id) is not scope):
            raise TaskSampleJournalUnavailable()
        scope.journal.close()

    def close(self) -> None:
        if os.getpid() != self._pid:
            raise TaskSampleJournalUnavailable()
        self._closed = True
        for scope in self._scopes.values():
            scope.journal.close()
