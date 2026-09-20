"""启动前复核、启动后确认与失败时的停止收尾；不删除容器。"""

import asyncio
import re

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker.docker_client import inspect_sandbox_container_by_id, start_sandbox_container
from app.services.runtime.sandbox.sandbox_isolation_policy import confirm_sandbox_isolation_policy
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from app.services.runtime.sandbox.sandbox_stop import SandboxStateSnapshot, confirm_sandbox_state, stop_and_confirm_sandbox


class SandboxStartUnconfirmed(RuntimeError):
    """启动结果未确认；停止证据独立记录，不推断命令是否执行过。"""

    def __init__(self, *, execution_token: str, container_id: str, stop_confirmed: bool) -> None:
        self.execution_token = execution_token
        self.container_id = container_id
        self.stop_confirmed = stop_confirmed
        super().__init__("Sandbox 启动结果未确认")


class SandboxStartCancelled(asyncio.CancelledError):
    """保持取消语义，并携带调用方后续恢复所需的执行身份和收尾证据。"""

    def __init__(self, *, execution_token: str, container_id: str, stop_confirmed: bool) -> None:
        self.execution_token = execution_token
        self.container_id = container_id
        self.stop_confirmed = stop_confirmed
        super().__init__("Sandbox 启动已取消")


async def _settle_start(
    *, request: CommandRequest, execution_token: str, container_id: str,
) -> bool:
    """停止服务会重新核对归属；未知目标绝不猜测性停止或删除。"""

    try:
        state = await stop_and_confirm_sandbox(
            request=request, execution_token=execution_token, expected_container_id=container_id,
        )
        return state.stopped
    except Exception:  # noqa: BLE001 -- 收尾失败仅记录未确认，不泄漏daemon错误。
        return False


async def _wait_for_settlement(task: asyncio.Task[bool]) -> tuple[bool, bool]:
    """重复取消不能打断停止收尾；记住取消请求供外层继续传播。"""

    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def start_and_confirm_sandbox(
    *, request: CommandRequest, execution_token: str, expected_container_id: str,
) -> SandboxStateSnapshot:
    """仅启动已复核的created目标；返回running或快速exited快照。"""

    # 建立本次调用自己的请求副本，避免等待期间外部修改原列表影响收尾。
    if not isinstance(request, CommandRequest):
        raise TypeError("request 必须是 CommandRequest")
    frozen_request = CommandRequest.model_validate(request.model_dump())
    spec = build_sandbox_create_spec(request=frozen_request, execution_token=execution_token)
    if not isinstance(expected_container_id, str) or re.fullmatch(r"[0-9a-f]{64}", expected_container_id) is None:
        raise ValueError("expected_container_id 必须是完整小写容器 ID")

    start_attempted = False
    try:
        text = await inspect_sandbox_container_by_id(container_id=expected_container_id)
        confirm_sandbox_isolation_policy(
            request=frozen_request, execution_token=execution_token,
            container_id=expected_container_id, inspect_stdout=text,
        )
        # 同份响应再核对严格阶段标志，拒绝矛盾的Paused/Restarting/Dead。
        before = confirm_sandbox_state(container_id=expected_container_id, inspect_stdout=text, spec=spec)
        if before.status != "created":
            raise ValueError("目标不是未启动容器")

        # 标志必须放在await之前；响应失败不能证明daemon没有启动。
        start_attempted = True
        await start_sandbox_container(container_id=expected_container_id)
        after = confirm_sandbox_state(
            container_id=expected_container_id, spec=spec,
            inspect_stdout=await inspect_sandbox_container_by_id(container_id=expected_container_id),
        )
        if after.status not in ("running", "exited"):
            raise ValueError("未取得启动后的状态")
        # exited只表示生命周期状态，不代表命令退出码为0。
        return after
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- 收尾后脱敏或继续传播取消。
        cancelled = isinstance(error, asyncio.CancelledError)
        stop_confirmed = False
        if start_attempted:
            # CLI内部先回收客户端，随后本层独立停止容器并确认。
            # 不自动删除；停止超时或失联仍保留原token/ID供后续恢复。
            task = asyncio.create_task(_settle_start(
                request=frozen_request, execution_token=execution_token, container_id=expected_container_id,
            ), name="sandbox-start-settlement")
            stop_confirmed, cancelled_during_settlement = await _wait_for_settlement(task)
            cancelled = cancelled or cancelled_during_settlement
        error_type = SandboxStartCancelled if cancelled else SandboxStartUnconfirmed
        raise error_type(execution_token=execution_token, container_id=expected_container_id,
                         stop_confirmed=stop_confirmed) from None
