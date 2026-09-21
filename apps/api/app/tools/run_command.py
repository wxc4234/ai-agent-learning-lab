"""异步受限命令适配器；本课不注册到同步工具调度器。"""

import asyncio

from app.services.runtime.sandbox.command_recovery_journal import (
    CommandRecoveryJournal,
    CommandRecoveryJournalUnavailable,
)
from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_command import (
    SandboxCommandCancelled,
    SandboxCommandRecovery,
    SandboxCommandUnconfirmed,
    run_sandbox_command,
)
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from app.tools.errors import SafeToolExecutionError


# 复用唯一参数契约，不另建可能漂移的工具Schema。
RunCommandArguments = CommandRequest


class CommandToolExecutionError(SafeToolExecutionError):
    """安全错误协议之外保留内部恢复证据，不直接序列化异常对象。"""

    def __init__(self, code: str, *, recovery: SandboxCommandRecovery | None = None) -> None:
        self.recovery = recovery
        super().__init__(code)


async def run_command(
    *,
    argv: list[str],
    recovery_journal: CommandRecoveryJournal,
    working_directory: str = ".",
) -> str:
    """执行受限命令，在异常离开适配器之前保存内部恢复证据。"""

    # 记录容器来自服务端拥有者，不属于CommandRequest或模型参数。
    if not isinstance(recovery_journal, CommandRecoveryJournal):
        raise CommandToolExecutionError(
            "command_recovery_unavailable",
        )

    try:
        request = CommandRequest(
            argv=argv,
            working_directory=working_directory,
        )
        # 仅纯校验；真实执行身份仍由统一入口生成。
        build_sandbox_create_spec(
            request=request,
            execution_token="0" * 32,
        )
    except (TypeError, ValueError):
        raise CommandToolExecutionError(
            "command_request_rejected",
        ) from None

    try:
        # 必须在任何外部执行之前取得记录位置。
        # 容量满或作用域关闭时，命令不能启动。
        record_index = recovery_journal.reserve(request)
    except CommandRecoveryJournalUnavailable:
        raise CommandToolExecutionError(
            "command_recovery_unavailable",
        ) from None

    try:
        result = await run_sandbox_command(request=request)

    except SandboxCommandCancelled as error:
        # 先同步保存，再传播原取消对象。
        # 外层wait_for即使转换异常，也不会删除拥有者手中的记录。
        recovery_journal.finish(
            record_index,
            status="cancelled",
            recovery=error.recovery,
        )
        raise

    except asyncio.CancelledError:
        # 未取得身份时仍记录取消事实，不猜测外部操作是否发生。
        recovery_journal.finish(
            record_index,
            status="cancelled",
        )
        raise

    except SandboxCommandUnconfirmed as error:
        recovery = error.recovery
        recovery_journal.finish(
            record_index,
            status="unconfirmed",
            recovery=recovery,
        )

        if recovery.phase == "creating":
            code = "command_creation_unconfirmed"
        elif recovery.phase == "cleaning" and recovery.command is not None:
            code = "command_cleanup_unconfirmed"
        elif recovery.phase == "executing":
            code = (
                "command_timeout_unconfirmed"
                if recovery.execution_reason == "timed_out"
                else "command_execution_unconfirmed"
            )
        else:
            code = "command_result_unavailable"

        raise CommandToolExecutionError(
            code,
            recovery=recovery,
        ) from None

    except Exception:  # noqa: BLE001 -- 记录未知失败，不向模型泄漏异常正文。
        recovery_journal.finish(
            record_index,
            status="unconfirmed",
        )
        raise CommandToolExecutionError(
            "command_result_unavailable",
        ) from None

    # 统一入口正常返回后，先保存完成事实，再构造公开输出。
    # 记录更新没有await，普通任务取消不会插入这两步之间。
    recovery_journal.finish(
        record_index,
        status="completed",
    )
    return result.command.model_dump_json()
