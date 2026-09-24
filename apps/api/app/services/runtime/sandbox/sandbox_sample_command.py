"""自建样例的内部命令编排；拥有来源，不接 Task、Workspace 或模型注册。"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.services.runtime.command.command_contracts import CommandRequest, CommandResult
from app.services.runtime.docker.docker_client import (
    create_sandbox_container,
    inspect_sandbox_container_by_id,
    is_sandbox_container_absent,
    remove_sandbox_container,
)
from app.services.runtime.sandbox.sandbox_cleanup import SandboxCleanupResult
from app.services.runtime.sandbox.sandbox_command_result import build_command_result
from app.services.runtime.sandbox.sandbox_execution import (
    SandboxExecutionCancelled,
    SandboxExecutionUnconfirmed,
    execute_created_sandbox,
)
from app.services.runtime.sandbox.sandbox_identity import parse_created_container_id
from app.services.runtime.sandbox.sandbox_isolation_policy import confirm_sandbox_isolation_policy
from app.services.runtime.sandbox.sandbox_sample import (
    SandboxSample,
    SandboxSampleCreationUnconfirmed,
    cleanup_sandbox_sample,
    confirm_sandbox_sample_source,
    create_sandbox_sample,
)
from app.services.runtime.sandbox.sandbox_spec import (
    build_sample_sandbox_create_spec,
    build_sandbox_create_spec,
)
from app.services.runtime.sandbox.sandbox_stop import confirm_sandbox_state


@dataclass(frozen=True, slots=True)
class SampleCommandRecovery:
    """调用方须保存的内部快照；不是公开响应、重试权或删除授权。"""

    execution_token: str
    container_name: str
    # 保留不可变请求，用于后续重新核对；不能保留调用者的可变列表。
    argv: tuple[str, ...] = field(repr=False)
    working_directory: str
    phase: Literal[
        "preparing", "creating", "executing", "adapting",
        "cleaning_container", "cleaning_sample",
    ] = "preparing"
    sample: SandboxSample | None = field(default=None, repr=False)
    # 样例工厂中途失败也保留自有现场；未完整登记的来源不自动清理。
    sample_token: str | None = None
    sample_root: Path | None = field(default=None, repr=False)
    container_id: str | None = None
    create_attempted: bool = False
    start_attempted: bool | None = None
    stop_confirmed: bool | None = None
    delete_attempted: bool = False
    container_absent: bool = False
    sample_cleaned: bool = False
    execution_reason: Literal["timed_out", "execution_failed"] | None = None
    # 输出成功事实与资源清理独立，后者失败不得丢失前者。
    command: CommandResult | None = None

    def build_request(self) -> CommandRequest:
        return CommandRequest(argv=list(self.argv), working_directory=self.working_directory)


@dataclass(frozen=True, slots=True)
class SampleCommandResult:
    """完整输出/退出事实与两个清理结果均确认后才正常返回。"""

    command: CommandResult
    cleanup: SandboxCleanupResult
    sample_token: str
    sample_cleaned: bool


class SampleCommandUnconfirmed(RuntimeError):
    def __init__(self, *, recovery: SampleCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("样例命令流程未完成确认")


class SampleCommandCancelled(asyncio.CancelledError):
    def __init__(self, *, recovery: SampleCommandRecovery) -> None:
        self.recovery = recovery
        super().__init__("样例命令流程已取消")


async def run_sample_sandbox_command(*, request: CommandRequest) -> SampleCommandResult:
    """保留固定探针入口；调用者不能传入目录或借用来源。"""

    return await _run_owned_sample_command(request=request, prepare_sample=create_sandbox_sample)


async def _prepare_in_thread(
    prepare: Callable[[], SandboxSample],
) -> tuple[SandboxSample | None, BaseException | None, bool]:
    """取消不能停止同步工作，必须收回结果后再传播，重复取消也等待收尾。"""

    task = asyncio.create_task(asyncio.to_thread(prepare))
    completion = asyncio.gather(task, return_exceptions=True)
    cancelled = False
    while True:
        try:
            result, = await asyncio.shield(completion)
            break
        except asyncio.CancelledError:
            cancelled = True
    if isinstance(result, BaseException):
        return None, result, cancelled
    return result, None, cancelled


async def _run_owned_sample_command(
    *, request: CommandRequest, prepare_sample: Callable[[], SandboxSample],
    prepare_in_thread: bool = False,
) -> SampleCommandResult:
    """仅可信内部工厂可移交本次新建的独占样例，不接受外部句柄或路径。

    准备完成后此调用拥有目标的生命周期；异常通过 recovery 交还所有权。
    Task 借用目录不进入本函数，工厂必须先正常归还借用再返回自有快照。
    """

    if not isinstance(request, CommandRequest):
        raise TypeError("request 必须是 CommandRequest")
    frozen_request = CommandRequest.model_validate(request.model_dump())
    token = uuid4().hex
    # 命令语法校验先于任何文件或 Docker 副作用。
    base = build_sandbox_create_spec(request=frozen_request, execution_token=token)
    recovery = SampleCommandRecovery(
        execution_token=token, container_name=base.container_name,
        argv=tuple(frozen_request.argv), working_directory=frozen_request.working_directory,
    )

    try:
        if prepare_in_thread:
            sample, error, cancelled = await _prepare_in_thread(prepare_sample)
            # 先收回线程创建结果，再处理取消；不丢失已完成或部分创建现场。
            if sample is not None:
                recovery = replace(
                    recovery, sample=sample, sample_token=sample.token, sample_root=sample.root,
                )
            if isinstance(error, SandboxSampleCreationUnconfirmed):
                recovery = replace(recovery, sample_token=error.token, sample_root=error.root)
            if cancelled:
                raise asyncio.CancelledError
            if error is not None:
                raise error
            if sample is None:
                raise ValueError("准备未返回样例")
        else:
            sample = prepare_sample()
        recovery = replace(
            recovery, sample=sample, sample_token=sample.token, sample_root=sample.root,
        )
        spec = build_sample_sandbox_create_spec(
            request=frozen_request, execution_token=token, sample=sample,
        )
        confirm_sandbox_sample_source(sample)
        # 文件系统与 daemon 无共同事务；失败不自动回滚、重复 create 或丢弃来源。
        recovery = replace(recovery, phase="creating", create_attempted=True)
        container_id = parse_created_container_id(await create_sandbox_container(spec=spec))
        # 在 inspect 前保存完整 ID，即使后续核对失败也不降级为只保留名称。
        recovery = replace(recovery, container_id=container_id)
        text = await inspect_sandbox_container_by_id(container_id=container_id)
        confirm_sandbox_isolation_policy(
            request=frozen_request, execution_token=token, container_id=container_id,
            inspect_stdout=text, sample=sample,
        )

        recovery = replace(recovery, phase="executing")
        execution = await execute_created_sandbox(
            request=frozen_request, execution_token=token, expected_container_id=container_id,
            sample=sample,
        )
        recovery = replace(
            recovery, phase="adapting", start_attempted=True, stop_confirmed=True,
        )
        command = build_command_result(execution)
        recovery = replace(recovery, phase="cleaning_container", command=command)

        # 删除边界重读归属及 exited/Pid=0，不能把执行返回的旧快照当作授权。
        state = confirm_sandbox_state(
            container_id=container_id, spec=spec,
            inspect_stdout=await inspect_sandbox_container_by_id(container_id=container_id),
        )
        if state.status != "exited" or not state.stopped:
            raise ValueError("未确认目标已退出")
        recovery = replace(recovery, delete_attempted=True)
        await remove_sandbox_container(container_id=container_id)
        if not await is_sandbox_container_absent(container_id=container_id):
            raise ValueError("未确认容器缺失")
        recovery = replace(recovery, phase="cleaning_sample", container_absent=True)
        cleanup_sandbox_sample(sample)
        recovery = replace(recovery, sample_cleaned=True)
        return SampleCommandResult(
            command=command,
            cleanup=SandboxCleanupResult(execution_token=token, container_id=container_id),
            sample_token=sample.token, sample_cleaned=True,
        )
    except SandboxSampleCreationUnconfirmed as error:
        recovery = replace(recovery, sample_token=error.token, sample_root=error.root)
        raise SampleCommandUnconfirmed(recovery=recovery) from None
    except SandboxExecutionCancelled as error:
        recovery = replace(
            recovery, start_attempted=error.start_attempted, stop_confirmed=error.stop_confirmed,
        )
        raise SampleCommandCancelled(recovery=recovery) from None
    except asyncio.CancelledError:
        # 创建/删除期间取消也可能已产生外部副作用；保留最后确认的事实。
        raise SampleCommandCancelled(recovery=recovery) from None
    except SandboxExecutionUnconfirmed as error:
        recovery = replace(
            recovery, start_attempted=error.start_attempted, stop_confirmed=error.stop_confirmed,
            execution_reason=error.reason,
        )
        raise SampleCommandUnconfirmed(recovery=recovery) from None
    except Exception:  # noqa: BLE001 -- 内部边界统一脱敏；不推断副作用未发生。
        raise SampleCommandUnconfirmed(recovery=recovery) from None
