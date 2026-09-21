"""已创建沙箱的订阅、启动、输出及退出编排；不创建或删除容器。"""

import asyncio
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Literal

from app.services.runtime.command.command_capture import CapturedCommandStreams
from app.services.runtime.command.command_contracts import COMMAND_TIMEOUT_SECONDS, CommandRequest
from app.services.runtime.docker.docker_attach_connection import open_docker_attach
from app.services.runtime.docker.docker_attach_http import build_docker_attach_request
from app.services.runtime.docker.docker_attach_stream import drain_docker_attach
from app.services.runtime.docker.docker_client import inspect_sandbox_container_by_id, start_sandbox_container
from app.services.runtime.sandbox.sandbox_exit import SandboxExitResult, confirm_sandbox_exit
from app.services.runtime.sandbox.sandbox_isolation_policy import confirm_sandbox_isolation_policy
from app.services.runtime.sandbox.sandbox_spec import SandboxCreateSpec, build_sandbox_create_spec
from app.services.runtime.sandbox.sandbox_stop import confirm_sandbox_state, stop_and_confirm_sandbox


SANDBOX_EXIT_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class SandboxExecutionResult:
    """正常读到帧边界EOF且确认退出后的事实；非零退出同样可以返回。"""

    streams: CapturedCommandStreams
    exit: SandboxExitResult
    # 覆盖本次编排到连接关闭的耗时，不冒充容器精确运行时长。
    duration_ms: int

    @property
    def succeeded(self) -> bool:
        return self.exit.succeeded


class SandboxExecutionUnconfirmed(RuntimeError):
    """不返回不完整输出；保留身份和停止证据供拥有者后续恢复。"""

    def __init__(self, *, execution_token: str, container_id: str,
                 start_attempted: bool, stop_confirmed: bool,
                 reason: Literal["timed_out", "execution_failed"]) -> None:
        self.execution_token = execution_token
        self.container_id = container_id
        self.start_attempted = start_attempted
        self.stop_confirmed = stop_confirmed
        self.reason = reason
        super().__init__("Sandbox 执行结果未确认")


class SandboxExecutionCancelled(asyncio.CancelledError):
    """维持取消语义；取消本身不代表容器停止。"""

    def __init__(self, *, execution_token: str, container_id: str,
                 start_attempted: bool, stop_confirmed: bool) -> None:
        self.execution_token = execution_token
        self.container_id = container_id
        self.start_attempted = start_attempted
        self.stop_confirmed = stop_confirmed
        super().__init__("Sandbox 执行已取消")


async def _confirm_created(*, request: CommandRequest, token: str,
                           container_id: str, spec: SandboxCreateSpec) -> None:
    text = await inspect_sandbox_container_by_id(container_id=container_id)
    confirm_sandbox_isolation_policy(
        request=request, execution_token=token, container_id=container_id, inspect_stdout=text,
    )
    state = confirm_sandbox_state(container_id=container_id, inspect_stdout=text, spec=spec)
    if state.status != "created":
        raise ValueError("目标不是未启动容器")


async def _wait_for_exit(*, request: CommandRequest, token: str,
                         container_id: str, spec: SandboxCreateSpec) -> SandboxExitResult:
    while True:
        text = await inspect_sandbox_container_by_id(container_id=container_id)
        state = confirm_sandbox_state(container_id=container_id, inspect_stdout=text, spec=spec)
        if state.status == "exited":
            return confirm_sandbox_exit(
                request=request, execution_token=token, container_id=container_id, inspect_stdout=text,
            )
        if state.status != "running":
            raise ValueError("启动后未取得running或exited证据")
        # 状态轮询与输出排空并发，避免输出背压阻塞容器退出。
        await asyncio.sleep(SANDBOX_EXIT_POLL_SECONDS)


async def _stop_owned(*, request: CommandRequest, token: str, container_id: str) -> bool:
    try:
        state = await stop_and_confirm_sandbox(
            request=request, execution_token=token, expected_container_id=container_id,
        )
        return state.stopped
    except Exception:  # noqa: BLE001 -- 停止失败只记录未确认，不泄漏daemon细节。
        return False


async def _settle_stop(*, request: CommandRequest, token: str, container_id: str) -> tuple[bool, bool]:
    # 既有停止服务内部CLI调用均有等待预算；重复取消不得遗留停止任务。
    task = asyncio.create_task(_stop_owned(request=request, token=token, container_id=container_id),
                               name="sandbox-execution-stop")
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def execute_created_sandbox(
    *, request: CommandRequest, execution_token: str, expected_container_id: str,
) -> SandboxExecutionResult:
    """内部执行入口；先订阅后启动，不自动重试、重建或删除目标。"""

    if not isinstance(request, CommandRequest):
        raise TypeError("request 必须是 CommandRequest")
    # 在首次await之前复制和重新校验，避免外部修改argv影响后续身份核对与停止。
    frozen_request = CommandRequest.model_validate(request.model_dump())
    spec = build_sandbox_create_spec(request=frozen_request, execution_token=execution_token)
    build_docker_attach_request(container_id=expected_container_id)
    started_at = perf_counter_ns()
    start_attempted = False
    budget = asyncio.timeout(COMMAND_TIMEOUT_SECONDS)

    try:
        async with budget:
            # 在读取任何容器输出之前核对身份及非TTY/隔离策略。
            await _confirm_created(request=frozen_request, token=execution_token,
                                   container_id=expected_container_id, spec=spec)
            async with (
                open_docker_attach(container_id=expected_container_id) as reader,
                asyncio.TaskGroup() as group,
            ):
                output = group.create_task(drain_docker_attach(reader), name="sandbox-attach-output")
                # 先让读取者开始消费；Moby的AttachStreams在升级响应前注册管道。
                # 这是服务端实现约束，不用任意sleep延迟猜测订阅就绪。
                await asyncio.sleep(0)
                # 握手等待期间目标可能变化，启动前再次复核同一完整ID。
                await _confirm_created(request=frozen_request, token=execution_token,
                                       container_id=expected_container_id, spec=spec)
                if output.done():
                    raise ValueError("启动前attach流已经结束")
                # 标志放在await之前：响应丢失不证明daemon未执行start。
                start_attempted = True
                await start_sandbox_container(container_id=expected_container_id)
                exit_result = await _wait_for_exit(
                    request=frozen_request, token=execution_token,
                    container_id=expected_container_id, spec=spec,
                )
                streams = await output
                # TaskGroup已收回输出任务，然后由连接拥有者关闭；关闭失败不能成功返回。
        return SandboxExecutionResult(
            streams=streams, exit=exit_result,
            duration_ms=(perf_counter_ns() - started_at) // 1_000_000,
        )
    except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- 收尾后统一安全错误/取消契约。
        cancelled = isinstance(error, asyncio.CancelledError)
        stop_confirmed = False
        if start_attempted:
            stop_confirmed, cancelled_during_stop = await _settle_stop(
                request=frozen_request, token=execution_token, container_id=expected_container_id,
            )
            cancelled = cancelled or cancelled_during_stop
        facts = {
            "execution_token": execution_token, "container_id": expected_container_id,
            "start_attempted": start_attempted, "stop_confirmed": stop_confirmed,
        }
        if cancelled:
            raise SandboxExecutionCancelled(**facts) from None
        raise SandboxExecutionUnconfirmed(
            **facts, reason="timed_out" if budget.expired() else "execution_failed",
        ) from None
