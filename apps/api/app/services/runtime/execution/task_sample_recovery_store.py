"""应用级持有 Task 快照 journal；单进程/单事件循环，无持久恢复。"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.services.runtime.sandbox.task_sample_command_journal import (
    TaskSampleCommandJournal,
    TaskSampleJournalUnavailable,
)

MAX_TASK_SAMPLE_RECOVERY_SCOPES = 32


@dataclass(frozen=True, slots=True)
class TaskSampleRecoveryScope:
    user_id: int
    conversation_id: str
    run_id: int
    journal: TaskSampleCommandJournal = field(repr=False)


class TaskSampleRecoveryStore:
    """按服务端 Run 绑定归属，拒绝自动淘汰或重新开放已关闭记录。

    同 Run 只应有一个可信请求拥有者；归属匹配不查询数据库、不替代授权。
    所有作用域保留到应用退出，容量耗尽时拒绝新 Run，不猜测资源已清理。
    """

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._closed = False
        self._scopes: dict[int, TaskSampleRecoveryScope] = {}

    def _check(self, user_id: int, conversation_id: str, run_id: int) -> None:
        if (
            os.getpid() != self._pid
            or type(user_id) is not int or user_id <= 0
            or type(run_id) is not int or run_id <= 0
            or not isinstance(conversation_id, str) or not conversation_id
        ):
            raise TaskSampleJournalUnavailable()

    def acquire(self, *, user_id: int, conversation_id: str, run_id: int) -> TaskSampleRecoveryScope:
        self._check(user_id, conversation_id, run_id)
        if self._closed:
            raise TaskSampleJournalUnavailable()
        scope = self._scopes.get(run_id)
        if scope is not None:
            if (scope.user_id, scope.conversation_id) != (user_id, conversation_id):
                raise TaskSampleJournalUnavailable()
            # 复用原 journal，绝不通过替换对象绕过其关闭或容量限制。
            return scope
        if len(self._scopes) >= MAX_TASK_SAMPLE_RECOVERY_SCOPES:
            raise TaskSampleJournalUnavailable()
        scope = TaskSampleRecoveryScope(
            user_id, conversation_id, run_id,
            TaskSampleCommandJournal(user_id=user_id, conversation_id=conversation_id),
        )
        self._scopes[run_id] = scope
        return scope

    def get(self, *, user_id: int, conversation_id: str, run_id: int) -> TaskSampleRecoveryScope | None:
        self._check(user_id, conversation_id, run_id)
        scope = self._scopes.get(run_id)
        if scope is None or (scope.user_id, scope.conversation_id) != (user_id, conversation_id):
            return None
        return scope

    def close_scope(self, scope: TaskSampleRecoveryScope) -> None:
        if (
            os.getpid() != self._pid or not isinstance(scope, TaskSampleRecoveryScope)
            or self._scopes.get(scope.run_id) is not scope
        ):
            raise TaskSampleJournalUnavailable()
        # 同对象关闭可重复；不删除 pending、失败或已完成记录，也不碰资源。
        scope.journal.close()

    @contextmanager
    def request_scope(self, *, user_id: int, conversation_id: str, run_id: int) -> Iterator[TaskSampleRecoveryScope]:
        scope = self.acquire(user_id=user_id, conversation_id=conversation_id, run_id=run_id)
        try:
            yield scope
        finally:
            self.close_scope(scope)

    def close(self) -> None:
        if os.getpid() != self._pid:
            raise TaskSampleJournalUnavailable()
        self._closed = True
        for scope in self._scopes.values():
            scope.journal.close()
