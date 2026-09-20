"""复核创建阶段的执行配置；不调用 Docker，也不授权启动。"""

import json

from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.sandbox_identity import (
    SandboxContainerIdentity,
    SandboxIdentityError,
    confirm_created_sandbox_identity,
)
from app.services.runtime.sandbox_spec import (
    APPROVED_SANDBOX_IMAGE,
    build_sandbox_create_spec,
)


# 来自当前批准镜像的实际 Config.Env。
# 它是入口程序启动时的环境，不是 env -i 后最终命令的环境。
# 更换批准镜像时必须重新读取、审核并更新，不能自动信任新镜像。
APPROVED_IMAGE_ENVIRONMENT = (
    (
        "PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:"
        "/usr/sbin:/usr/bin:/sbin:/bin"
    ),
    "LANG=C.UTF-8",
    "GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305",
    "PYTHON_VERSION=3.12.14",
    (
        "PYTHON_SHA256="
        "5c8462af5790baf43a321a1559dbe0db06d1be4300fb85fb53c40060668e548a"
    ),
)


class SandboxExecutionPolicyError(ValueError):
    """身份或执行配置不足以通过本课复核。"""

    def __init__(self) -> None:
        # 不将原始命令、环境值或 inspect 内容放进错误信息。
        super().__init__("无法确认 Sandbox 执行配置符合策略")


def confirm_sandbox_execution_policy(
    *,
    request: CommandRequest,
    execution_token: str,
    container_id: str,
    inspect_stdout: str,
) -> SandboxContainerIdentity:
    """核对身份及执行配置，返回本次检查对应的身份快照。"""

    # 根据调用方保留的原请求重新生成服务端规格。
    # 不接受调用方直接提供任意 Docker 参数作为“期望配置”。
    spec = build_sandbox_create_spec(
        request=request,
        execution_token=execution_token,
    )

    try:
        # 先复用严格解析和创建状态检查。
        # 此处已经限制长度，并拒绝重复字段、非标准数值、
        # 多个目标、身份不匹配及非 created 状态。
        identity = confirm_created_sandbox_identity(
            container_id=container_id,
            inspect_stdout=inspect_stdout,
            spec=spec,
        )
    except SandboxIdentityError:
        raise SandboxExecutionPolicyError() from None

    # 对同一份已通过严格解析的不可变字符串读取配置。
    # 不重新查询，避免身份与配置来自两次不同的响应。
    item = json.loads(inspect_stdout)[0]
    config = item["Config"]

    # 镜像后的参数就是预期 Config.Cmd：
    # -i、--、固定环境赋值以及原命令，顺序和内容都必须一致。
    # spec 由上面的可信构造函数生成，不解析外部提供的任意规格。
    image_index = spec.argv.index(APPROVED_SANDBOX_IMAGE)
    expected_command = list(spec.argv[image_index + 1:])

    if (
        config.get("User") != "10001:10001"
        or config.get("WorkingDir") != "/tmp"
        or config.get("Entrypoint") != ["/usr/bin/env"]
        or config.get("Cmd") != expected_command
    ):
        raise SandboxExecutionPolicyError()

    # 布尔字段严格检查，不能让 JSON 的 0 冒充 False。
    # 当前执行方式不开放交互式 stdin 或终端。
    for field in ("Tty", "OpenStdin", "StdinOnce", "AttachStdin"):
        if config.get(field) is not False:
            raise SandboxExecutionPolicyError()

    environment = config.get("Env")

    if (
        not isinstance(environment, list)
        or not all(isinstance(value, str) for value in environment)
    ):
        raise SandboxExecutionPolicyError()

    # 环境条目的顺序不影响本策略；内容、数量必须完全一致。
    # 比较列表而非集合，因此重复变量也不能被静默去重后接受。
    if sorted(environment) != sorted(APPROVED_IMAGE_ENVIRONMENT):
        raise SandboxExecutionPolicyError()

    # 当前批准镜像没有健康检查。
    # 不允许额外配置另一个自动执行入口。
    if config.get("Healthcheck") is not None:
        raise SandboxExecutionPolicyError()

    # 返回身份快照供后续步骤关联目标。
    # 这不是长期授权，也不表示 HostConfig/挂载已通过检查。
    return identity
