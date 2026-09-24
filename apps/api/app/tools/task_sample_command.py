"""Task 快照命令的内部工具适配；不在本模块注册模型能力。"""

import asyncio
from dataclasses import dataclass, field

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.execution.scoped_task_sample_command import run_scoped_task_sample_command
from app.services.runtime.execution.task_sample_recovery_store import TaskSampleRecoveryStore
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandUnconfirmed
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.errors import SafeToolExecutionError
from app.tools.context import ToolExecutionContext

# 使用既有参数契约；服务端身份、Run 和恢复容器不进入模型 Schema。
RunTaskSampleCommandArguments = CommandRequest


@dataclass(frozen=True, slots=True, repr=False)
class TaskSampleCommandToolContext:
    """由请求拥有者注入的内部上下文，不是可序列化的授权凭据。"""

    user_id: int
    conversation_id: str
    run_id: int
    store: TaskSampleRecoveryStore = field(repr=False)
    bindings: TaskSampleBindings = field(repr=False)
    expected_context: ToolExecutionContext | None = field(default=None, repr=False)


async def run_task_sample_command_tool(
    *, argv: list[str], context: TaskSampleCommandToolContext,
    working_directory: str = '.',
) -> str:
    """公开结果只投影 CommandResult；所有权及失败证据留在服务端 journal。"""

    if (
        not isinstance(context, TaskSampleCommandToolContext)
        or not isinstance(context.store, TaskSampleRecoveryStore)
        or not isinstance(context.bindings, TaskSampleBindings)
    ):
        raise SafeToolExecutionError('command_recovery_unavailable')
    try:
        request = CommandRequest(argv=argv, working_directory=working_directory)
        # 纯校验在应用作用域分配之前，真实执行令牌仍由生命周期入口创建。
        build_sandbox_create_spec(request=request, execution_token='0' * 32)
    except (TypeError, ValueError):
        raise SafeToolExecutionError('command_request_rejected') from None

    try:
        result = await run_scoped_task_sample_command(
            request=request, user_id=context.user_id,
            conversation_id=context.conversation_id, run_id=context.run_id,
            bindings=context.bindings, store=context.store,
            **({} if context.expected_context is None else {"expected_context": context.expected_context}),
        )
    except asyncio.CancelledError:
        # 底层先收回准备线程结果并保存记录；取消不转换为普通工具错误。
        raise
    except TaskSampleJournalUnavailable:
        # 存储/作用域/记录预算门禁在准备前拒绝，才能说明命令未启动。
        raise SafeToolExecutionError('command_recovery_unavailable') from None
    except SampleCommandUnconfirmed as error:
        recovery = error.recovery
        if recovery.phase == 'preparing':
            code = 'task_command_preparation_unconfirmed'
        elif recovery.phase == 'creating':
            code = 'command_creation_unconfirmed'
        elif recovery.phase == 'executing':
            code = (
                'command_timeout_unconfirmed'
                if recovery.execution_reason == 'timed_out'
                else 'command_execution_unconfirmed'
            )
        elif recovery.phase in ('cleaning_container', 'cleaning_sample') and recovery.command is not None:
            code = 'command_cleanup_unconfirmed'
        else:
            code = 'command_result_unavailable'
        # 不在公开错误对象上附加 recovery；记录已由底层写入应用拥有者。
        raise SafeToolExecutionError(code) from None
    except Exception:  # noqa: BLE001 -- 无阶段证据的错误保留未知，不回显原异常。
        raise SafeToolExecutionError('command_result_unavailable') from None

    try:
        # completed 包含非零退出。只输出命令契约，不序列化整个内部结果。
        return result.command.model_dump_json()
    except Exception:  # noqa: BLE001 -- 结果投影失败不覆盖已记录的真实完成事实。
        raise SafeToolExecutionError('command_result_unavailable') from None
