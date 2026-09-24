"""从应用级存储取得请求 journal 后执行；调用方负责请求作用域收尾。"""

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.execution.task_sample_recovery_store import TaskSampleRecoveryStore
from app.services.runtime.sandbox.recorded_task_sample_command import run_recorded_task_sample_command
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandResult
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext


async def run_scoped_task_sample_command(
    *, request: CommandRequest, user_id: int, conversation_id: str, run_id: int,
    bindings: TaskSampleBindings, store: TaskSampleRecoveryStore,
    expected_context: ToolExecutionContext | None = None,
) -> SampleCommandResult:
    """身份和 Run 来自服务端；一次命令结束不自动关闭整个请求的 journal。"""

    if not isinstance(store, TaskSampleRecoveryStore):
        raise TaskSampleJournalUnavailable()
    scope = store.acquire(user_id=user_id, conversation_id=conversation_id, run_id=run_id)
    return await run_recorded_task_sample_command(
        request=request, user_id=user_id, conversation_id=conversation_id,
        bindings=bindings, journal=scope.journal,
        **({} if expected_context is None else {"expected_context": expected_context}),
    )
