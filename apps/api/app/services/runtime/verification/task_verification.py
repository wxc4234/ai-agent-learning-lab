"""Task固定验证的内部编排；不接收源码、宿主路径或外部快照。"""

from functools import partial

from app.services.runtime.sandbox.sandbox_sample_command import _run_owned_sample_command
from app.services.runtime.verification.contracts import VerificationRequest, build_verification_command
from app.services.runtime.verification.sandbox_verification import (
    SandboxVerificationResult, adapt_sample_verification,
)
from app.services.runtime.verification.task_verification_input import create_task_verification_snapshot
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext


async def run_task_verification(
    *, request: VerificationRequest, context: ToolExecutionContext,
    bindings: TaskSampleBindings,
) -> SandboxVerificationResult:
    """同一所有者完成重新授权、准备、执行和清理；不重复准备或自动重试。

    同步工厂在受管理线程内重新授权并借用Task，归还后才创建独立快照。
    生命周期服务等待准备线程收尾，负责容器身份核对、停止与清理。
    失败/取消原样传播SampleCommand恢复信息，报告适配失败保留完整回执。
    本调用不持有跨越文件I/O或容器执行的数据库事务。
    """
    frozen = VerificationRequest.model_validate(request.model_dump())
    command = build_verification_command(frozen.plan_id)
    execution = await _run_owned_sample_command(
        request=command,
        prepare_sample=partial(
            create_task_verification_snapshot, context=context, bindings=bindings,
        ),
        prepare_in_thread=True,
    )
    # 只有完整生命周期正常返回后才解析报告；取消和未知清理不能被改成通过。
    return adapt_sample_verification(execution)
