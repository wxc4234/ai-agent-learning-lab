"""验证执行前预留，异常或公开投影之前同步保存生命周期证据。"""

import asyncio

from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandCancelled, SampleCommandUnconfirmed
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from app.services.runtime.verification.contracts import VerificationRequest, build_verification_command
from app.services.runtime.verification.sandbox_verification import SandboxVerificationResult, VerificationAdaptationError
from app.services.runtime.verification.task_verification import run_task_verification
from app.services.runtime.verification.verification_journal import TaskVerificationJournal
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext


async def run_recorded_task_verification(
    *, request: VerificationRequest, context: ToolExecutionContext,
    bindings: TaskSampleBindings, journal: TaskVerificationJournal,
) -> SandboxVerificationResult:
    """登记不替代重新授权；保存与传播之间没有await，不重试、不自动清理。"""
    if not isinstance(journal, TaskVerificationJournal):
        raise TaskSampleJournalUnavailable()
    journal.require_context(context)
    frozen = VerificationRequest.model_validate(request.model_dump())
    index = journal.reserve(build_verification_command(frozen.plan_id))
    try:
        result = await run_task_verification(request=frozen, context=context, bindings=bindings)
    except SampleCommandCancelled as error:
        journal.finish(index, status='cancelled', recovery=error.recovery)
        raise
    except asyncio.CancelledError:
        journal.finish(index, status='cancelled')
        raise
    except SampleCommandUnconfirmed as error:
        journal.finish(index, status='unconfirmed', recovery=error.recovery)
        raise
    except VerificationAdaptationError as error:
        # 生命周期已正常结束，只是报告适配失败；不能丢弃已确认清理的回执。
        journal.finish(index, status='completed', result=error.execution)
        raise
    except Exception:  # noqa: BLE001 -- 没有取得证据时保留未知，禁止猜测资源不存在。
        journal.finish(index, status='unconfirmed')
        raise RuntimeError('task_verification_unconfirmed') from None
    journal.finish(index, status='completed', result=result.execution)
    return result
