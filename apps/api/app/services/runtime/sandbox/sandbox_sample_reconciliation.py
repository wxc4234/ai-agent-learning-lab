"""样例命令失败后的内部只读诊断；不授权执行、重试或资源清理。"""

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from app.services.runtime.sandbox.sandbox_command import SandboxCommandRecovery
from app.services.runtime.sandbox.sandbox_command_reconciliation import (
    reconcile_sandbox_command,
)
from app.services.runtime.sandbox.sandbox_identity import SandboxContainerIdentity
from app.services.runtime.sandbox.sandbox_sample import (
    SandboxSample,
    SandboxSampleStatus,
    observe_sandbox_sample,
)
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandRecovery
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec


@dataclass(frozen=True, slots=True)
class SampleCommandSnapshot:
    """两次独立观察的结果，不声称文件系统与 Docker 存在原子快照。"""

    recovery: SampleCommandRecovery
    container_status: Literal[
        "not_attempted", "created", "running", "exited", "absent", "unconfirmed",
    ]
    container_id: str | None
    # discovered 只表示未知 ID 时通过 created 阶段核对的候选，不覆盖原记录。
    id_source: Literal["record", "discovered", "unknown"]
    identity: SandboxContainerIdentity | None
    sample_status: SandboxSampleStatus


class SampleReconciliationCancelled(asyncio.CancelledError):
    def __init__(self, *, recovery: SampleCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("样例命令状态核对已取消")


def validate_sample_command_recovery(recovery: SampleCommandRecovery) -> None:
    """所有输入一致性校验在 Docker/文件系统读取之前完成。"""

    if not isinstance(recovery, SampleCommandRecovery):
        raise TypeError("recovery 必须是 SampleCommandRecovery")
    if not isinstance(recovery.argv, tuple):
        raise TypeError("恢复参数必须是不可变快照")
    spec = build_sandbox_create_spec(
        request=recovery.build_request(), execution_token=recovery.execution_token,
    )
    if recovery.container_name != spec.container_name:
        raise ValueError("恢复名称与执行身份不一致")
    if recovery.phase not in (
        "preparing", "creating", "executing", "adapting", "cleaning_container", "cleaning_sample",
    ):
        raise ValueError("恢复阶段无效")
    for flag in (recovery.create_attempted, recovery.delete_attempted,
                 recovery.container_absent, recovery.sample_cleaned):
        if type(flag) is not bool:
            raise ValueError("恢复标志必须是布尔值")
    for flag in (recovery.start_attempted, recovery.stop_confirmed):
        if flag is not None and type(flag) is not bool:
            raise ValueError("恢复标志必须是布尔值或未知")
    if recovery.container_id is not None:
        if (
            not isinstance(recovery.container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", recovery.container_id) is None
            or not recovery.create_attempted
        ):
            raise ValueError("已知容器必须具有完整 ID 和创建尝试记录")
    elif recovery.create_attempted and recovery.phase != "creating":
        raise ValueError("创建阶段之外不能丢失已知 ID")
    if not recovery.create_attempted and recovery.phase != "preparing":
        raise ValueError("未尝试创建的阶段不一致")
    if recovery.sample is not None and (
        not isinstance(recovery.sample, SandboxSample)
        or recovery.sample_token != recovery.sample.token
        or recovery.sample_root != recovery.sample.root
    ):
        raise ValueError("样例句柄与恢复记录不一致")
    # sample=None 的部分创建现场只保留原始定位，不拿 raw path 做文件访问。


async def reconcile_sample_command(*, recovery: SampleCommandRecovery) -> SampleCommandSnapshot:
    """分别读取容器和来源；普通读失败返回未知，取消继续向上传播。"""

    validate_sample_command_recovery(recovery)
    # 先读来源元数据，再等待 Docker；两者可能来自不同时刻，不能作为操作租约。
    sample_status = observe_sandbox_sample(recovery.sample)
    if not recovery.create_attempted:
        return SampleCommandSnapshot(
            recovery=recovery, container_status="not_attempted", container_id=None,
            id_source="unknown", identity=None, sample_status=sample_status,
        )

    # 复用已有严格身份/状态读取。已知 ID 始终按 ID 查；未知 ID 只允许
    # created 候选发现，随后按完整 ID 再核对，不从通用错误推断 absent。
    known_id = recovery.container_id
    legacy = SandboxCommandRecovery(
        execution_token=recovery.execution_token, container_name=recovery.container_name,
        container_id=known_id, phase="creating" if known_id is None else "executing",
    )
    try:
        result = await reconcile_sandbox_command(request=recovery.build_request(), recovery=legacy)
    except asyncio.CancelledError:
        raise SampleReconciliationCancelled(recovery=recovery) from None
    except Exception:  # noqa: BLE001 -- 保留来源观察，Docker 错误不补造成 absent。
        return SampleCommandSnapshot(
            recovery=recovery, container_status="unconfirmed", container_id=known_id,
            id_source="record" if known_id is not None else "unknown",
            identity=None, sample_status=sample_status,
        )
    return SampleCommandSnapshot(
        recovery=recovery, container_status=result.status,
        container_id=known_id if known_id is not None else result.identity.container_id,
        id_source="record" if known_id is not None else "discovered",
        identity=result.identity, sample_status=sample_status,
    )
