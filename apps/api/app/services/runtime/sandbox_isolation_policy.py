"""复核当前 Sandbox 的隔离与资源策略，不调用 Docker 或启动容器。"""

import json

from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.sandbox_execution_policy import (
    SandboxExecutionPolicyError,
    confirm_sandbox_execution_policy,
)
from app.services.runtime.sandbox_identity import SandboxContainerIdentity


# 与当前创建规格保持一致。
# 暂时要求选项文本完全匹配，不自行解释未知或冲突的挂载选项。
EXPECTED_TMPFS = {
    "/home/agent": (
        "rw,noexec,nosuid,nodev,"
        "size=16m,mode=0700,uid=10001,gid=10001"
    ),
    "/tmp": (
        "rw,noexec,nosuid,nodev,"
        "size=16m,mode=0700,uid=10001,gid=10001"
    ),
}


class SandboxIsolationPolicyError(ValueError):
    """无法确认本课要求的隔离与资源配置。"""

    def __init__(self) -> None:
        super().__init__("无法确认 Sandbox 隔离配置符合策略")


def _require_exact(
    mapping: dict,
    field: str,
    expected: object,
) -> None:
    """同时检查类型和值，避免 False 与 0 被视为相等。"""

    actual = mapping.get(field)

    if type(actual) is not type(expected) or actual != expected:
        raise SandboxIsolationPolicyError()


def _require_empty_list(
    mapping: dict,
    field: str,
) -> None:
    """可选列表只允许未配置或空列表，不接受其他假值。"""

    actual = mapping.get(field)

    if actual is not None and (
        not isinstance(actual, list) or len(actual) != 0
    ):
        raise SandboxIsolationPolicyError()


def _check_reported_mounts(value: object) -> None:
    """拒绝挂载摘要中的 bind、volume 和额外目标。"""

    if not isinstance(value, list):
        raise SandboxIsolationPolicyError()

    destinations: set[str] = set()

    for mount in value:
        if not isinstance(mount, dict):
            raise SandboxIsolationPolicyError()

        destination = mount.get("Destination")

        # 先收窄类型，再用于集合操作，避免非字符串或缺失值混入。
        if (
            not isinstance(destination, str)
            or destination not in EXPECTED_TMPFS
            or destination in destinations
            or mount.get("Type") != "tmpfs"
            or mount.get("RW") is not True
        ):
            raise SandboxIsolationPolicyError()

        destinations.add(destination)

    # created 阶段不能只靠挂载摘要证明 tmpfs 已实际挂载。
    # 必须同时检查 HostConfig.Tmpfs；摘要中若有条目，只允许指定目标。


def confirm_sandbox_isolation_policy(
    *,
    request: CommandRequest,
    execution_token: str,
    container_id: str,
    inspect_stdout: str,
) -> SandboxContainerIdentity:
    """对同一份响应完成执行配置、隔离和资源配置复核。"""

    try:
        # 复用严格 JSON、身份、created 状态和执行配置检查。
        # 后续读取的仍是这份不可变字符串，不再次查询 Docker。
        identity = confirm_sandbox_execution_policy(
            request=request,
            execution_token=execution_token,
            container_id=container_id,
            inspect_stdout=inspect_stdout,
        )
    except SandboxExecutionPolicyError:
        raise SandboxIsolationPolicyError() from None

    item = json.loads(inspect_stdout)[0]
    host = item.get("HostConfig")

    if not isinstance(host, dict):
        raise SandboxIsolationPolicyError()

    # 布尔策略不接受 0/1、字符串或缺失字段。
    for field, expected in {
        "Privileged": False,
        "ReadonlyRootfs": True,
        "Init": True,
        "AutoRemove": False,
        "PublishAllPorts": False,
    }.items():
        _require_exact(host, field, expected)

    # 不共享宿主或其他容器的网络、PID、IPC、UTS 命名空间。
    # 这里只接受本项目当前策略，不做跨 Docker 部署的自动兼容。
    for field, expected in {
        "NetworkMode": "none",
        "PidMode": "",
        "IpcMode": "private",
        "UTSMode": "",
    }.items():
        _require_exact(host, field, expected)

    _require_exact(host, "CapDrop", ["ALL"])
    _require_exact(
        host,
        "SecurityOpt",
        ["no-new-privileges:true"],
    )

    # 禁止额外能力、设备、补充组和其他挂载入口。
    # Docker 可选列表可能表现为 null 或 []，二者均表示未配置。
    for field in (
        "CapAdd",
        "Devices",
        "DeviceRequests",
        "DeviceCgroupRules",
        "GroupAdd",
        "Binds",
        "Mounts",
        "VolumesFrom",
    ):
        _require_empty_list(host, field)

    port_bindings = host.get("PortBindings")
    if port_bindings is not None and (
        not isinstance(port_bindings, dict) or len(port_bindings) != 0
    ):
        raise SandboxIsolationPolicyError()

    # 按 inspect 中的整数单位核对，不接受浮点数、布尔值或字符串。
    for field, expected in {
        "NanoCpus": 500_000_000,
        "Memory": 128 * 1024 * 1024,
        "MemorySwap": 128 * 1024 * 1024,
        "PidsLimit": 32,
        "ShmSize": 8 * 1024 * 1024,
    }.items():
        _require_exact(host, field, expected)

    restart_policy = host.get("RestartPolicy")
    if not isinstance(restart_policy, dict):
        raise SandboxIsolationPolicyError()

    _require_exact(restart_policy, "Name", "no")
    _require_exact(restart_policy, "MaximumRetryCount", 0)

    log_config = host.get("LogConfig")
    if not isinstance(log_config, dict):
        raise SandboxIsolationPolicyError()

    _require_exact(log_config, "Type", "none")

    # 只读根之外，仅允许当前规格声明的两个 tmpfs 目标。
    # 字典相等还会拒绝额外目标、缺失目标及任意选项变化。
    _require_exact(host, "Tmpfs", EXPECTED_TMPFS)
    _check_reported_mounts(item.get("Mounts"))

    # 防止镜像配置额外声明卷，避免创建隐式匿名 volume。
    volumes = item["Config"].get("Volumes")
    if volumes is not None and (
        not isinstance(volumes, dict) or len(volumes) != 0
    ):
        raise SandboxIsolationPolicyError()

    return identity
