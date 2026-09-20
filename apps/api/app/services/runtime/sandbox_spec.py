"""构造 Docker Sandbox 创建参数；不访问 Docker 或文件系统。"""

from dataclasses import dataclass
import re

from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.command_environment import (
    build_posix_command_environment,
)


# 只接受上一课实际验收的镜像。
# 摘要固定内容；允许列表表达服务端是否批准使用该内容。
APPROVED_SANDBOX_IMAGE = (
    "python@sha256:"
    "392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
)


@dataclass(frozen=True, slots=True)
class SandboxCreateSpec:
    """一次执行的容器名称、归属标记和完整创建参数。"""

    container_name: str
    execution_token: str
    argv: tuple[str, ...]


def build_sandbox_create_spec(
    *,
    request: CommandRequest,
    execution_token: str,
    image: str = APPROVED_SANDBOX_IMAGE,
) -> SandboxCreateSpec:
    """将已校验命令与服务端策略组合成不可变创建规格。"""

    # execution_token 由服务端为每次执行生成，不来自模型参数。
    # 名称和标签都保留它，为后续定位、校验归属及清理提供线索。
    if (
        not isinstance(execution_token, str)
        or re.fullmatch(r"[0-9a-f]{32}", execution_token) is None
    ):
        raise ValueError("执行标记必须是32位小写十六进制字符串")

    # 不能只检查“看起来像摘要”：其他镜像可能带有不同的配置或工具。
    if not isinstance(image, str) or image != APPROVED_SANDBOX_IMAGE:
        raise ValueError("只能使用服务端批准的 Sandbox 镜像摘要")

    if not isinstance(request, CommandRequest):
        raise TypeError("request 必须是 CommandRequest")

    # CommandRequest 及其中的列表可变。
    # 重新校验当前字段，并取得新的参数列表，避免使用构造后被改坏的请求。
    validated = CommandRequest.model_validate(request.model_dump())

    # 尚未接入项目挂载，不假装支持项目中的相对工作目录。
    if validated.working_directory != ".":
        raise ValueError("当前 Sandbox 仅支持默认临时工作目录")

    executable = validated.argv[0]

    # 使用容器内绝对程序路径，不依赖 PATH 搜索。
    # 禁止等号，避免 env 将程序位置误解释成额外环境赋值。
    if not executable.startswith("/") or "=" in executable:
        raise ValueError("程序必须使用不含等号的容器内绝对路径")

    container_name = f"agent-sandbox-{execution_token}"

    environment = build_posix_command_environment(
        home_directory="/home/agent",
        temporary_directory="/tmp",
    )

    # 环境值由已有白名单函数生成，不接收模型 env 覆盖。
    environment_arguments = tuple(
        f"{name}={value}"
        for name, value in environment.items()
    )

    # 镜像前全部是服务端固定 Docker 选项。
    # 不使用宿主挂载、端口映射、特权模式或自动删除。
    # 后续先确认停止状态，再按容器身份显式清理。
    argv = (
        "docker",
        "create",
        f"--name={container_name}",
        "--label=ai-agent-learning-lab.role=sandbox",
        f"--label=ai-agent-learning-lab.execution={execution_token}",
        "--platform=linux/arm64",
        "--pull=never",
        "--init",
        "--user=10001:10001",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--cpus=0.5",
        "--memory=128m",
        "--memory-swap=128m",
        "--pids-limit=32",
        "--shm-size=8m",
        (
            "--tmpfs=/home/agent:rw,noexec,nosuid,nodev,"
            "size=16m,mode=0700,uid=10001,gid=10001"
        ),
        (
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,"
            "size=16m,mode=0700,uid=10001,gid=10001"
        ),
        "--workdir=/tmp",
        "--restart=no",
        "--stop-timeout=2",
        "--log-driver=none",
        "--entrypoint=/usr/bin/env",
        image,
        # 从此处开始是容器程序参数，不再是 Docker 选项。
        "-i",
        "--",
        *environment_arguments,
        *validated.argv,
    )

    return SandboxCreateSpec(
        container_name=container_name,
        execution_token=execution_token,
        argv=argv,
    )
