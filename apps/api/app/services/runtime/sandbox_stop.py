"""按已知执行身份停止容器，并以重新查询的状态确认结果。"""

import re
from dataclasses import dataclass

from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.docker_client import inspect_sandbox_container_by_id, stop_sandbox_container
from app.services.runtime.sandbox_identity import SandboxContainerIdentity, SandboxIdentityError, read_sandbox_identity
from app.services.runtime.sandbox_spec import SandboxCreateSpec, build_sandbox_create_spec


class SandboxStopUnconfirmed(RuntimeError):
    """未取得停止证据；不能据此删除或释放执行资源。"""

    def __init__(self, *, execution_token: str, container_id: str) -> None:
        self.execution_token = execution_token
        self.container_id = container_id
        super().__init__("Sandbox 停止结果未确认")


@dataclass(frozen=True, slots=True)
class SandboxStateSnapshot:
    """可信daemon单次响应的状态快照，不是持续停止的保证。"""

    identity: SandboxContainerIdentity
    status: str
    pid: int

    @property
    def stopped(self) -> bool:
        return self.status in ("created", "exited") and self.pid == 0


def confirm_sandbox_state(
    *, container_id: str, inspect_stdout: str, spec: SandboxCreateSpec,
) -> SandboxStateSnapshot:
    """仅接受一致的created/running/exited状态；异常中间态保守拒绝。"""

    identity, state = read_sandbox_identity(
        container_id=container_id, inspect_stdout=inspect_stdout, spec=spec,
    )
    status, pid, running = state.get("Status"), state.get("Pid"), state.get("Running")
    if not isinstance(status, str) or status not in ("created", "running", "exited"):
        raise SandboxIdentityError()
    if type(pid) is not int or pid < 0 or type(running) is not bool:
        raise SandboxIdentityError()
    for field in ("Paused", "Restarting", "Dead"):
        if state.get(field) is not False:
            raise SandboxIdentityError()
    if status == "running":
        if not running or pid == 0:
            raise SandboxIdentityError()
    elif running or pid != 0:
        raise SandboxIdentityError()
    return SandboxStateSnapshot(identity=identity, status=status, pid=pid)


async def stop_and_confirm_sandbox(
    *, request: CommandRequest, execution_token: str, expected_container_id: str,
) -> SandboxStateSnapshot:
    """先授权归属，再请求停止并重新核对；不自动删除或重试。"""

    spec = build_sandbox_create_spec(request=request, execution_token=execution_token)
    if not isinstance(expected_container_id, str) or re.fullmatch(r"[0-9a-f]{64}", expected_container_id) is None:
        raise ValueError("expected_container_id 必须是完整小写容器 ID")
    try:
        before = confirm_sandbox_state(
            container_id=expected_container_id, spec=spec,
            inspect_stdout=await inspect_sandbox_container_by_id(container_id=expected_container_id),
        )
        # 已经取得停止证据时无需发送stop；仅停止阶段允许exited。
        # 不放宽既有created清理服务的契约。
        if before.stopped:
            return before
        await stop_sandbox_container(container_id=expected_container_id)
        after = confirm_sandbox_state(
            container_id=expected_container_id, spec=spec,
            inspect_stdout=await inspect_sandbox_container_by_id(container_id=expected_container_id),
        )
        if not after.stopped:
            raise SandboxStopUnconfirmed(execution_token=execution_token, container_id=expected_container_id)
        return after
    except Exception:  # noqa: BLE001 -- 外部生命周期错误脱敏，取消继续传播。
        # 客户端超时/取消不证明daemon操作已完成。保留token/ID供后续查询。
        raise SandboxStopUnconfirmed(execution_token=execution_token, container_id=expected_container_id) from None
