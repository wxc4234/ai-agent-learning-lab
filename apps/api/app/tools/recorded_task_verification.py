"""装配带恢复记录的验证工具；仍不修改全局或请求注册表。"""

from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandUnconfirmed
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from app.services.runtime.verification.contracts import VerificationRequest
from app.services.runtime.verification.recorded_task_verification import run_recorded_task_verification
from app.services.runtime.verification.sandbox_verification import SandboxVerificationResult
from app.services.runtime.verification.verification_journal import TaskVerificationJournal
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import ToolDefinition
from app.tools.task_verification import RecordedVerificationExecutor, make_task_verification_definition


def make_recorded_task_verification_executor(
    *, bindings: TaskSampleBindings, journal: TaskVerificationJournal,
) -> RecordedVerificationExecutor:
    """宿主须保留journal；内部记录不放入工具参数或Observation。"""
    if not isinstance(bindings, TaskSampleBindings) or not isinstance(journal, TaskVerificationJournal):
        raise TypeError('需要服务端样例绑定和验证记录')

    async def execute(*, request: VerificationRequest, context: ToolExecutionContext) -> SandboxVerificationResult:
        try:
            return await run_recorded_task_verification(
                request=request, context=context, bindings=bindings, journal=journal,
            )
        except TaskSampleJournalUnavailable:
            raise SafeToolExecutionError('verification_recovery_unavailable') from None
        except SampleCommandUnconfirmed as error:
            # 此时记录层已经同步保存原恢复对象，公开错误只用白名单分类。
            recovery = error.recovery
            code = 'verification_execution_unconfirmed'
            if recovery.phase in ('cleaning_container', 'cleaning_sample'):
                code = 'verification_cleanup_unconfirmed'
            elif recovery.execution_reason == 'timed_out':
                code = 'verification_timeout_unconfirmed'
            raise SafeToolExecutionError(code) from None
        except Exception:  # noqa: BLE001 -- 不公开私有异常；CancelledError不属于Exception，继续传播。
            raise SafeToolExecutionError('verification_result_unavailable') from None

    return execute


def make_recorded_task_verification_definition(
    *, bindings: TaskSampleBindings, journal: TaskVerificationJournal,
) -> ToolDefinition:
    """保留独立定义入口，与请求装配复用同一执行适配。"""
    return make_task_verification_definition(make_recorded_task_verification_executor(
        bindings=bindings, journal=journal,
    ))
