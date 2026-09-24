"""可信内部 Task 快照命令入口；不注册模型工具或开放宿主路径参数。"""

from functools import partial

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_sample_command import (
    SampleCommandResult,
    _run_owned_sample_command,
)
from app.services.runtime.sandbox.task_sample_snapshot import create_task_sandbox_snapshot
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext


async def run_task_sample_command(
    *, request: CommandRequest, user_id: int, conversation_id: str,
    bindings: TaskSampleBindings,
    expected_context: ToolExecutionContext | None = None,
) -> SampleCommandResult:
    """以当前服务端身份导出一次快照，并移交给内部容器生命周期。

    命令先校验再产生资源。数据库授权与 Task 来源借用只在线程准备阶段；
    容器只读挂载独立快照。失败/取消须保存 SampleCommandRecovery，
    不因 Task 已归还就删除仍可能被容器使用的目标。
    """

    return await _run_owned_sample_command(
        request=request,
        prepare_sample=partial(
            create_task_sandbox_snapshot, user_id=user_id,
            conversation_id=conversation_id, bindings=bindings,
            **({} if expected_context is None else {"expected_context": expected_context}),
        ),
        prepare_in_thread=True,
    )
