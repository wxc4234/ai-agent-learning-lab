"""严格解析 Docker 创建与 inspect 响应，不调用 Docker。"""

from dataclasses import dataclass
import json
import re

from app.services.runtime.sandbox_spec import (
    APPROVED_SANDBOX_IMAGE,
    SandboxCreateSpec,
)


# 这里只限制传入解析器的文本长度。
# 后续 Docker 调用层仍须在读取阶段限制原始响应字节数。
MAX_INSPECT_CHARACTERS = 65_536


class SandboxIdentityError(ValueError):
    """响应不足以确认身份；不表示容器一定不存在。"""

    def __init__(self) -> None:
        # 不把 Docker 原始响应、标签或解析异常正文暴露给调用方。
        super().__init__("无法确认 Sandbox 容器身份或创建状态")


@dataclass(frozen=True, slots=True)
class SandboxContainerIdentity:
    """一次成功核对得到的身份快照，不是永久授权证明。"""

    container_id: str
    container_name: str
    execution_token: str
    image: str


def parse_created_container_id(stdout: str) -> str:
    """只接受完整小写容器 ID，以及可选的一个行结束符。"""

    if not isinstance(stdout, str):
        raise SandboxIdentityError()

    # 不使用 strip，避免把额外空格、多行或混入日志静默忽略。
    match = re.fullmatch(
        r"([0-9a-f]{64})(?:\r?\n)?",
        stdout,
    )

    if match is None:
        raise SandboxIdentityError()

    return match.group(1)


def _unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """拒绝重复字段，避免同一身份字段具有多种解释。"""

    result: dict[str, object] = {}

    for key, value in pairs:
        if key in result:
            raise SandboxIdentityError()
        result[key] = value

    return result


def _reject_json_constant(value: str) -> object:
    """拒绝 Python JSON 解码器默认允许的 NaN 和 Infinity。"""

    raise SandboxIdentityError()


def read_sandbox_identity(
    *,
    container_id: str,
    inspect_stdout: str,
    spec: SandboxCreateSpec,
) -> tuple[SandboxContainerIdentity, dict]:
    """严格核对归属并返回状态字段；生命周期判断由调用方完成。"""

    if not isinstance(spec, SandboxCreateSpec):
        raise TypeError("spec 必须是服务端生成的 SandboxCreateSpec")

    # 这里接收已经解析过的 ID，不再允许行结束符。
    if (
        not isinstance(container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    ):
        raise SandboxIdentityError()

    if (
        not isinstance(inspect_stdout, str)
        or not inspect_stdout
        or len(inspect_stdout) > MAX_INSPECT_CHARACTERS
    ):
        raise SandboxIdentityError()

    try:
        payload = json.loads(
            inspect_stdout,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError):
        raise SandboxIdentityError() from None

    # 本函数只核对一个目标，不能从多个返回项中“挑一个像的”。
    if not isinstance(payload, list) or len(payload) != 1:
        raise SandboxIdentityError()

    item = payload[0]
    if not isinstance(item, dict):
        raise SandboxIdentityError()

    config = item.get("Config")
    state = item.get("State")

    if not isinstance(config, dict) or not isinstance(state, dict):
        raise SandboxIdentityError()

    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise SandboxIdentityError()

    # 同时核对 ID、名称、用途标签、执行标记及批准的镜像引用。
    # Docker inspect 的 Name 带有前导斜杠。
    if (
        item.get("Id") != container_id
        or item.get("Name") != f"/{spec.container_name}"
        or config.get("Image") != APPROVED_SANDBOX_IMAGE
        or labels.get("ai-agent-learning-lab.role") != "sandbox"
        or labels.get("ai-agent-learning-lab.execution")
        != spec.execution_token
    ):
        raise SandboxIdentityError()

    identity = SandboxContainerIdentity(
        container_id=container_id,
        container_name=spec.container_name,
        execution_token=spec.execution_token,
        image=APPROVED_SANDBOX_IMAGE,
    )
    return identity, state


def confirm_created_sandbox_identity(
    *,
    container_id: str,
    inspect_stdout: str,
    spec: SandboxCreateSpec,
) -> SandboxContainerIdentity:
    """保留创建阶段约束，不将运行中或已退出目标当作创建完成。"""

    identity, state = read_sandbox_identity(
        container_id=container_id, inspect_stdout=inspect_stdout, spec=spec,
    )
    pid = state.get("Pid")
    if (
        state.get("Status") != "created"
        or state.get("Running") is not False
        or type(pid) is not int
        or pid != 0
    ):
        raise SandboxIdentityError()
    return identity


def recover_created_sandbox_identity(
    *,
    inspect_stdout: str,
    spec: SandboxCreateSpec,
) -> SandboxContainerIdentity:
    """原创建 ID 未知时，从响应中发现候选并核对创建阶段身份。"""

    if not isinstance(spec, SandboxCreateSpec):
        raise TypeError("spec 必须是服务端生成的 SandboxCreateSpec")

    # 提取候选 ID 之前也要执行长度与严格 JSON 校验，
    # 不能先用宽松解析接受重复字段或非标准数值。
    if (
        not isinstance(inspect_stdout, str)
        or not inspect_stdout
        or len(inspect_stdout) > MAX_INSPECT_CHARACTERS
    ):
        raise SandboxIdentityError()

    try:
        payload = json.loads(
            inspect_stdout,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError):
        raise SandboxIdentityError() from None

    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise SandboxIdentityError()

    candidate_id = payload[0].get("Id")

    # JSON 字段可能缺失、为 null 或其他类型；先检查并收窄为 str，
    # 再交给身份确认函数校验完整 ID 格式，不能用 str() 强制转换。
    if not isinstance(candidate_id, str):
        raise SandboxIdentityError()

    # 候选 ID 不能直接作为成功结果返回。
    # 复用已有函数，检查 ID 格式、名称、标签、镜像与创建状态。
    # 同一份有长度限制的不可变字符串会解析两次，
    # 这里优先复用完整核对规则，避免维护两份身份判断逻辑。
    return confirm_created_sandbox_identity(
        container_id=candidate_id,
        inspect_stdout=inspect_stdout,
        spec=spec,
    )
