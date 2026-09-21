"""按明确生命周期阶段显式清理 Sandbox，不接入自动失败补偿。"""

import asyncio
import re
from dataclasses import dataclass

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker.docker_client import (
    inspect_sandbox_container,
    inspect_sandbox_container_by_id,
    is_sandbox_container_absent,
    remove_sandbox_container,
)
from app.services.runtime.sandbox.sandbox_identity import confirm_created_sandbox_identity
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from app.services.runtime.sandbox.sandbox_stop import confirm_sandbox_state


class SandboxCleanupUnconfirmed(RuntimeError):
    """未能确认清理完成；保留定位信息，不推断删除是否发生。"""

    def __init__(self, *, execution_token: str, container_id: str) -> None:
        self.execution_token = execution_token
        self.container_id = container_id
        super().__init__("Sandbox 清理结果未确认")


@dataclass(frozen=True, slots=True)
class SandboxCleanupResult:
    """删除回执核对且随后成功查询无该 ID 时的完成快照。"""

    execution_token: str
    container_id: str


class SandboxCleanupCancelled(asyncio.CancelledError):
    """保留取消语义与删除尝试事实；取消不证明删除已撤销。"""

    def __init__(self, *, execution_token: str, container_id: str, delete_attempted: bool) -> None:
        self.execution_token = execution_token
        self.container_id = container_id
        self.delete_attempted = delete_attempted
        super().__init__("Sandbox 清理已取消，结果未确认")


async def cleanup_created_sandbox(
    *,
    request: CommandRequest,
    execution_token: str,
    expected_container_id: str,
) -> SandboxCleanupResult:
    """重新核对本次创建身份后，按完整 ID 非强制删除并确认缺失。"""

    # 校验在任何 Docker 调用之前；不允许缺失 ID 时降级成按名称删除。
    spec = build_sandbox_create_spec(request=request, execution_token=execution_token)
    if (
        not isinstance(expected_container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_container_id) is None
    ):
        raise ValueError("expected_container_id 必须是完整小写容器 ID")

    try:
        text = await inspect_sandbox_container(execution_token=spec.execution_token)
        identity = confirm_created_sandbox_identity(
            container_id=expected_container_id, inspect_stdout=text, spec=spec,
        )
        # 查询与删除不是原子事务。完整 ID 避免删除同名替换对象；
        # 不加 --force，不主动停止查询后被其他主体启动的容器。
        # 仍不能防止宿主高权限主体在两次请求之间修改目标状态。
        await remove_sandbox_container(container_id=identity.container_id)
        if not await is_sandbox_container_absent(container_id=identity.container_id):
            raise SandboxCleanupUnconfirmed(
                execution_token=spec.execution_token, container_id=identity.container_id,
            )
        return SandboxCleanupResult(
            execution_token=spec.execution_token, container_id=identity.container_id,
        )
    except Exception:  # noqa: BLE001 -- 外部操作统一脱敏，取消仍向上传播。
        # 超时可能发生在删除已生效之后；不重试，也不将查询失败当作不存在。
        raise SandboxCleanupUnconfirmed(
            execution_token=spec.execution_token, container_id=expected_container_id,
        ) from None


async def cleanup_exited_sandbox(
    *,
    request: CommandRequest,
    execution_token: str,
    expected_container_id: str,
) -> SandboxCleanupResult:
    """仅清理重新确认归属且一致exited/Pid=0的目标，不停止或强制删除。"""

    if not isinstance(request, CommandRequest):
        raise TypeError("request 必须是 CommandRequest")
    # 首次await前冻结并重校验上下文，不使用调用方可变列表或旧退出快照授权删除。
    frozen_request = CommandRequest.model_validate(request.model_dump())
    spec = build_sandbox_create_spec(request=frozen_request, execution_token=execution_token)
    if (
        not isinstance(expected_container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_container_id) is None
    ):
        raise ValueError("expected_container_id 必须是完整小写容器 ID")

    delete_attempted = False
    try:
        text = await inspect_sandbox_container_by_id(container_id=expected_container_id)
        state = confirm_sandbox_state(
            container_id=expected_container_id, inspect_stdout=text, spec=spec,
        )
        # created也属于stopped，但由独立的created-only入口处理。
        # 清理只依赖归属与停止事实，不以退出码0作为门槛，也不推断业务成功。
        if state.status != "exited" or not state.stopped:
            raise SandboxCleanupUnconfirmed(
                execution_token=spec.execution_token, container_id=expected_container_id,
            )

        # 查询和删除不是原子事务；完整ID防同名替换，非强制rm拒绝删除运行目标。
        # 在await之前记录尝试，响应丢失或取消不能证明daemon没有执行删除。
        delete_attempted = True
        await remove_sandbox_container(container_id=state.identity.container_id)
        if not await is_sandbox_container_absent(container_id=state.identity.container_id):
            raise SandboxCleanupUnconfirmed(
                execution_token=spec.execution_token, container_id=expected_container_id,
            )
        return SandboxCleanupResult(
            execution_token=spec.execution_token, container_id=state.identity.container_id,
        )
    except asyncio.CancelledError:
        # 低层客户端已负责回收CLI；本层不继续查询、重试或伪造成功。
        raise SandboxCleanupCancelled(
            execution_token=spec.execution_token, container_id=expected_container_id,
            delete_attempted=delete_attempted,
        ) from None
    except Exception:  # noqa: BLE001 -- 未知删除结果保留执行身份并脱敏，不自动重试。
        raise SandboxCleanupUnconfirmed(
            execution_token=spec.execution_token, container_id=expected_container_id,
        ) from None
