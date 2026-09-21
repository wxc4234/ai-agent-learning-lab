"""只读查询失败命令的当前资源状态，不恢复执行或补造命令结果。"""

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker.docker_client import (
    inspect_sandbox_container_by_id,
    is_sandbox_container_absent,
)
from app.services.runtime.sandbox.sandbox_command import SandboxCommandRecovery
from app.services.runtime.sandbox.sandbox_identity import SandboxContainerIdentity
from app.services.runtime.sandbox.sandbox_reconciliation import reconcile_created_sandbox
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from app.services.runtime.sandbox.sandbox_stop import confirm_sandbox_state


@dataclass(frozen=True, slots=True)
class SandboxCommandSnapshot:
    """检查时刻的内部快照；不能作为后续删除授权或持续状态保证。"""

    recovery: SandboxCommandRecovery
    # absent仅表示成功查询时该完整ID不存在，不证明之前的清理成功。
    status: Literal["created", "running", "exited", "absent"]
    identity: SandboxContainerIdentity | None


class SandboxCommandReconciliationUnconfirmed(RuntimeError):
    """查询失败、身份不匹配或状态不一致，均不能推断目标缺失。"""

    def __init__(self, *, recovery: SandboxCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("Sandbox 命令状态未确认")


class SandboxCommandReconciliationCancelled(asyncio.CancelledError):
    """取消继续传播，保留原恢复身份。"""

    def __init__(self, *, recovery: SandboxCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("Sandbox 命令状态核对已取消")


async def reconcile_sandbox_command(
    *, request: CommandRequest, recovery: SandboxCommandRecovery,
) -> SandboxCommandSnapshot:
    """仅供持有原请求和服务端恢复证据的内部调用方使用。"""

    if not isinstance(request, CommandRequest):
        raise TypeError("request 必须是 CommandRequest")
    if not isinstance(recovery, SandboxCommandRecovery):
        raise TypeError("recovery 必须是 SandboxCommandRecovery")
    # 首次await之前复制请求；原阶段是诊断信息，不作为当前状态依据。
    frozen_request = CommandRequest.model_validate(request.model_dump())
    spec = build_sandbox_create_spec(
        request=frozen_request, execution_token=recovery.execution_token,
    )
    if recovery.container_name != spec.container_name:
        raise ValueError("恢复名称与执行身份不一致")
    if recovery.phase not in ("creating", "executing", "adapting", "cleaning"):
        raise ValueError("恢复阶段无效")
    container_id = recovery.container_id
    if container_id is None:
        if recovery.phase != "creating":
            raise ValueError("创建阶段之外必须保留完整容器ID")
    elif not isinstance(container_id, str) or re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise ValueError("恢复目标必须是完整小写容器ID")

    try:
        if container_id is None:
            # 复用创建响应丢失后的保守核对，不放宽为按名称接受running/exited。
            identity = await reconcile_created_sandbox(
                request=frozen_request, execution_token=spec.execution_token,
            )
            container_id = identity.container_id
        elif await is_sandbox_container_absent(container_id=container_id):
            # 查询成功才报告absent；不将inspect异常文本解释成“不存在”。
            return SandboxCommandSnapshot(recovery=recovery, status="absent", identity=None)

        # 缺失查询和inspect并非原子事务；其间消失仍报告未确认，不重试。
        text = await inspect_sandbox_container_by_id(container_id=container_id)
        state = confirm_sandbox_state(
            container_id=container_id, inspect_stdout=text, spec=spec,
        )
        return SandboxCommandSnapshot(
            recovery=recovery, status=state.status, identity=state.identity,
        )
    except asyncio.CancelledError:
        raise SandboxCommandReconciliationCancelled(recovery=recovery) from None
    except Exception:  # noqa: BLE001 -- 保留原证据，屏蔽Docker输出及异常正文。
        raise SandboxCommandReconciliationUnconfirmed(recovery=recovery) from None
