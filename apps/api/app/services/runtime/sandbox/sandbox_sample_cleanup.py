"""内部所有者显式释放失败样例；不接受诊断快照或未知容器候选。"""

import asyncio
from dataclasses import dataclass, replace

from app.services.runtime.docker.docker_client import (
    inspect_sandbox_container_by_id,
    is_sandbox_container_absent,
    remove_sandbox_container,
)
from app.services.runtime.sandbox.sandbox_sample import (
    cleanup_sandbox_sample,
    confirm_sandbox_sample_source,
)
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandRecovery
from app.services.runtime.sandbox.sandbox_sample_reconciliation import validate_sample_command_recovery
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from app.services.runtime.sandbox.sandbox_stop import confirm_sandbox_state

# 同一事件循环中的显式清理互斥；不替代来源登记和每次操作前的身份复核。
_CLEANING: set[str] = set()


@dataclass(frozen=True, slots=True)
class SampleCleanupResult:
    recovery: SampleCommandRecovery


class SampleCleanupUnconfirmed(RuntimeError):
    def __init__(self, *, recovery: SampleCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("样例清理未完成确认")


class SampleCleanupCancelled(asyncio.CancelledError):
    def __init__(self, *, recovery: SampleCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("样例清理已取消")


async def cleanup_sample_command(*, recovery: SampleCommandRecovery) -> SampleCleanupResult:
    """仅供原内部所有者调用，要求一个样例仅供原命令使用且没有并发执行。

    恢复记录是定位信息，不是授权凭据；不得暴露成接收任意记录的 API。
    不接管部分创建现场，不按名称发现目标，不停止或重放命令。
    """

    validate_sample_command_recovery(recovery)
    sample = recovery.sample
    container_id = recovery.container_id
    if sample is None or container_id is None or sample.token in _CLEANING:
        raise SampleCleanupUnconfirmed(recovery=recovery)
    _CLEANING.add(sample.token)
    try:
        # 旧记录中的 absent/cleaned 不能省略本轮核对；保留命令输出和执行失败事实。
        recovery = replace(recovery, container_absent=False, sample_cleaned=False)
        confirm_sandbox_sample_source(sample)
        spec = build_sandbox_create_spec(
            request=recovery.build_request(), execution_token=recovery.execution_token,
        )
        if not await is_sandbox_container_absent(container_id=container_id):
            state = confirm_sandbox_state(
                container_id=container_id, spec=spec,
                inspect_stdout=await inspect_sandbox_container_by_id(container_id=container_id),
            )
            if not state.stopped or state.status not in ("created", "exited"):
                raise ValueError("容器尚未停止")
            # await 期间来源可能被替换；删除容器前再次拒绝变化的现场。
            confirm_sandbox_sample_source(sample)
            recovery = replace(recovery, phase="cleaning_container", delete_attempted=True)
            await remove_sandbox_container(container_id=container_id)
            # 外部删除与本地来源清理没有共同事务；回执丢失即停止，保留现场。
            if not await is_sandbox_container_absent(container_id=container_id):
                raise ValueError("未确认容器缺失")
        recovery = replace(recovery, phase="cleaning_sample", container_absent=True)
        # cleanup 本身重新核对登记、完整结构、inode 与权限，只删除固定样例文件。
        cleanup_sandbox_sample(sample)
        return SampleCleanupResult(recovery=replace(recovery, sample_cleaned=True))
    except asyncio.CancelledError:
        raise SampleCleanupCancelled(recovery=recovery) from None
    except Exception:  # noqa: BLE001 -- 不将查询失败或删除回执丢失解释为缺失。
        raise SampleCleanupUnconfirmed(recovery=recovery) from None
    finally:
        _CLEANING.remove(sample.token)
