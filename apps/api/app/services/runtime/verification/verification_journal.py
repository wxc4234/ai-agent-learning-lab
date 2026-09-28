"""Task验证的请求级记录：复用命令生命周期记录，并锁定完整Task上下文。"""

from dataclasses import replace

from app.services.runtime.sandbox.task_sample_command_journal import (
    TaskSampleCommandJournal, TaskSampleJournalUnavailable,
)
from app.tools.context import ToolExecutionContext


class TaskVerificationJournal(TaskSampleCommandJournal):
    """单请求、单事件循环持有；关闭只阻止新执行，不删除记录或资源。

    records沿用命令生命周期语义：completed表示执行及清理已确认，
    不表示测试passed或公开投影成功。command_json保留完整报告流供内部核对。
    拥有者必须在请求退出后保留未处置记录；本类不是持久恢复或执行授权。
    """

    def __init__(self, *, context: ToolExecutionContext) -> None:
        if type(context) is not ToolExecutionContext or any(
            type(value) is not str or not value
            for value in (context.conversation_id, context.workspace_id, context.task_id)
        ):
            raise TaskSampleJournalUnavailable()
        super().__init__(user_id=context.user_id, conversation_id=context.conversation_id)
        self._context = replace(context)

    def require_context(self, context: ToolExecutionContext) -> None:
        if (type(context) is not ToolExecutionContext or type(context.user_id) is not int
                or context != self._context):
            raise TaskSampleJournalUnavailable()
        self.require_scope(user_id=context.user_id, conversation_id=context.conversation_id)
