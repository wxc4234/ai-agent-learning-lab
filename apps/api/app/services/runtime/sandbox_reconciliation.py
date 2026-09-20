"""只读核对创建结果，不创建、启动或删除容器。"""

import re

from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.docker_client import inspect_sandbox_container
from app.services.runtime.sandbox_creation import SandboxCreationUnconfirmed
from app.services.runtime.sandbox_identity import (
    SandboxContainerIdentity,
    confirm_created_sandbox_identity,
    recover_created_sandbox_identity,
)
from app.services.runtime.sandbox_spec import build_sandbox_create_spec


async def reconcile_created_sandbox(
    *,
    request: CommandRequest,
    execution_token: str,
    expected_container_id: str | None = None,
) -> SandboxContainerIdentity:
    """用原执行信息查询目标，只有身份和创建状态通过才返回。"""

    # request 与 token 来自调用方保留的原执行上下文。
    # 在服务内部重新构造可信规格，不接受模型提供任意 Docker 参数。
    # 本函数不会执行规格中的 create 命令。
    spec = build_sandbox_create_spec(
        request=request,
        execution_token=execution_token,
    )

    # 已知 ID 是额外约束，必须在查询前验证。
    # 不接受短 ID、行结束符，也不把无效值降级为“ID 未知”。
    if expected_container_id is not None and (
        not isinstance(expected_container_id, str)
        or re.fullmatch(
            r"[0-9a-f]{64}",
            expected_container_id,
        ) is None
    ):
        raise ValueError("expected_container_id 必须是完整小写容器 ID")

    try:
        # 只按原 token 对应的名称查询一次，不循环重试。
        inspect_stdout = await inspect_sandbox_container(
            execution_token=spec.execution_token,
        )

        if expected_container_id is not None:
            # 已知原 ID 时必须保留这个约束，
            # 同名容器被替换后不能用新 ID 冒充原目标。
            return confirm_created_sandbox_identity(
                container_id=expected_container_id,
                inspect_stdout=inspect_stdout,
                spec=spec,
            )

        # 原创建响应丢失时，从当前响应取得候选 ID，
        # 完整核对名称、标签、镜像及 created 状态后才返回。
        return recover_created_sandbox_identity(
            inspect_stdout=inspect_stdout,
            spec=spec,
        )
    except Exception:  # noqa: BLE001 -- 核对失败统一保留未确认语义，不捕获取消。
        # 查询失败可能来自不存在、daemon 不可达、输出异常或身份不符。
        # 当前没有足够证据把这些情况解释成“可以重新创建”。
        raise SandboxCreationUnconfirmed(
            execution_token=spec.execution_token,
            container_name=spec.container_name,
        ) from None
