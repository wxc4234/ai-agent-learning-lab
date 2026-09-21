"""协调单次命令的创建、执行及清理，保留各阶段的已知事实。"""

import asyncio
from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

from app.services.runtime.command.command_contracts import (
    CommandRequest,
    CommandResult,
)
from app.services.runtime.sandbox.sandbox_cleanup import (
    SandboxCleanupCancelled,
    SandboxCleanupResult,
    cleanup_exited_sandbox,
)
from app.services.runtime.sandbox.sandbox_command_result import (
    build_command_result,
)
from app.services.runtime.sandbox.sandbox_creation import (
    create_and_confirm_sandbox,
)
from app.services.runtime.sandbox.sandbox_execution import (
    SandboxExecutionCancelled,
    SandboxExecutionUnconfirmed,
    execute_created_sandbox,
)
from app.services.runtime.sandbox.sandbox_spec import (
    build_sandbox_create_spec,
)


SandboxCommandPhase = Literal[
    "creating",
    "executing",
    "adapting",
    "cleaning",
]


@dataclass(frozen=True, slots=True)
class SandboxCommandRecovery:
    """内部恢复证据，不直接序列化给模型或浏览器。"""

    # 创建期间可能拿不到完整ID，仍需保留服务端生成的定位信息。
    execution_token: str
    container_name: str
    phase: SandboxCommandPhase
    container_id: str | None = None

    # None表示本层没有取得对应事实，不能当成False。
    start_attempted: bool | None = None
    stop_confirmed: bool | None = None
    delete_attempted: bool | None = None

    # 保留执行层已有的超时/失败分类，不根据错误正文猜测。
    execution_reason: Literal[
        "timed_out",
        "execution_failed",
    ] | None = None

    # 清理失败不能抹掉此前已确认的命令结果。
    command: CommandResult | None = None


@dataclass(frozen=True, slots=True)
class SandboxCommandResult:
    """正常完成编排后的内部结果，命令事实与清理凭据分别保存。"""

    command: CommandResult
    cleanup: SandboxCleanupResult


class SandboxCommandUnconfirmed(RuntimeError):
    """某阶段未完成确认；不表示没有发生外部副作用。"""

    def __init__(self, *, recovery: SandboxCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("Sandbox 命令流程未完成确认")


class SandboxCommandCancelled(asyncio.CancelledError):
    """保留取消语义，同时携带已经取得的恢复证据。"""

    def __init__(self, *, recovery: SandboxCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("Sandbox 命令流程已取消")


async def run_sandbox_command(
    *,
    request: CommandRequest,
) -> SandboxCommandResult:
    """创建一次、执行一次，并在正常取得结果后清理已退出容器。"""

    if not isinstance(request, CommandRequest):
        raise TypeError("request 必须是 CommandRequest")

    # 首次await前复制并重新校验，后续不再引用调用方的可变argv。
    frozen_request = CommandRequest.model_validate(request.model_dump())

    # 身份由服务生成，调用方不能选择或复用其他执行的token。
    execution_token = uuid4().hex
    spec = build_sandbox_create_spec(
        request=frozen_request,
        execution_token=execution_token,
    )
    recovery = SandboxCommandRecovery(
        execution_token=execution_token,
        container_name=spec.container_name,
        phase="creating",
    )

    # Docker操作没有跨阶段事务。下面每次await都可能已经产生副作用，
    # 因此不能用统一重试或无条件finally删除来模拟回滚。
    try:
        identity = await create_and_confirm_sandbox(
            request=frozen_request,
            execution_token=execution_token,
        )
        recovery = replace(
            recovery,
            phase="executing",
            container_id=identity.container_id,
        )

        execution = await execute_created_sandbox(
            request=frozen_request,
            execution_token=execution_token,
            expected_container_id=identity.container_id,
        )

        # 正常返回意味着执行层已确认退出并完成输出与连接收尾。
        # 这里保存停止事实，但它不是之后删除操作的授权快照。
        recovery = replace(
            recovery,
            phase="adapting",
            start_attempted=True,
            stop_confirmed=True,
        )
        command = build_command_result(execution)
        recovery = replace(
            recovery,
            phase="cleaning",
            command=command,
        )

        # 无论退出码是否为0，都清理正常完成执行的容器。
        # 清理服务必须重新查询归属及exited状态，不能只信旧结果。
        cleanup = await cleanup_exited_sandbox(
            request=frozen_request,
            execution_token=execution_token,
            expected_container_id=identity.container_id,
        )
        return SandboxCommandResult(
            command=command,
            cleanup=cleanup,
        )

    except SandboxExecutionCancelled as error:
        # 执行层已经完成它拥有的读取、连接与停止收尾。
        # 本层保留证据并继续取消，不再发起额外删除操作。
        recovery = replace(
            recovery,
            start_attempted=error.start_attempted,
            stop_confirmed=error.stop_confirmed,
        )
        raise SandboxCommandCancelled(recovery=recovery) from None

    except SandboxCleanupCancelled as error:
        recovery = replace(
            recovery,
            delete_attempted=error.delete_attempted,
        )
        raise SandboxCommandCancelled(recovery=recovery) from None

    except asyncio.CancelledError:
        # 创建阶段的取消可能发生在daemon已创建容器之后。
        # 即使没有完整ID，也必须保留原token和名称供只读核对。
        raise SandboxCommandCancelled(recovery=recovery) from None

    except SandboxExecutionUnconfirmed as error:
        recovery = replace(
            recovery,
            start_attempted=error.start_attempted,
            stop_confirmed=error.stop_confirmed,
            execution_reason=error.reason,
        )
        raise SandboxCommandUnconfirmed(recovery=recovery) from None

    except Exception:  # noqa: BLE001 -- 外部阶段统一安全异常，保留已有事实。
        # 普通清理异常没有提供delete_attempted，保持None而非猜测。
        # 已有command仍保存在recovery中，不输出底层异常正文。
        raise SandboxCommandUnconfirmed(recovery=recovery) from None
