"""创建 Sandbox 并确认创建阶段身份，不启动容器。"""

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.docker.docker_client import (
    create_sandbox_container,
    inspect_sandbox_container,
)
from app.services.runtime.sandbox.sandbox_identity import (
    SandboxContainerIdentity,
    confirm_created_sandbox_identity,
    parse_created_container_id,
)
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec


class SandboxCreationUnconfirmed(RuntimeError):
    """创建结果无法确认；不表示容器不存在或可以安全重试。"""

    def __init__(
        self,
        *,
        execution_token: str,
        container_name: str,
    ) -> None:
        # 保留服务端生成的定位信息，供后续恢复流程使用。
        # 不附带原始 Docker 输出或底层异常正文。
        self.execution_token = execution_token
        self.container_name = container_name
        super().__init__("Sandbox 创建结果未确认")


async def create_and_confirm_sandbox(
    *,
    request: CommandRequest,
    execution_token: str,
) -> SandboxContainerIdentity:
    """创建一次容器，只有创建响应与 inspect 一致才返回身份。"""

    # 所有纯校验放在外部操作之前。
    # 这里失败表示本次函数尚未发起 Docker 创建请求，
    # 但不能据此断言同一 token 对应的历史容器不存在。
    spec = build_sandbox_create_spec(
        request=request,
        execution_token=execution_token,
    )

    # 从开始调用 create 起，就进入可能发生外部副作用的阶段。
    # Docker 操作无法与后端代码形成原子事务；
    # 后续异常统一表示本次流程未能确认最终创建结果。
    try:
        create_stdout = await create_sandbox_container(spec=spec)

        container_id = parse_created_container_id(create_stdout)

        # 按本次执行名称定位，再用创建返回的完整 ID 核对。
        # 即使同名目标被替换，也不能只凭名称接受它。
        inspect_stdout = await inspect_sandbox_container(
            execution_token=spec.execution_token,
        )

        return confirm_created_sandbox_identity(
            container_id=container_id,
            inspect_stdout=inspect_stdout,
            spec=spec,
        )
    except Exception:  # noqa: BLE001 -- 外部副作用阶段统一报告未确认，不捕获取消。
        # 不自动重试，不按名称删除，也不返回未经确认的部分结果。
        # asyncio.CancelledError 不属于 Exception，会继续向上传播。
        raise SandboxCreationUnconfirmed(
            execution_token=spec.execution_token,
            container_name=spec.container_name,
        ) from None
    