"""调用本机 Docker 控制客户端，并在退出前完成进程与管道收尾。"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.services.runtime.command.command_capture import (
    CapturedCommandStreams,
    drain_command_streams,
)
from app.services.runtime.command.command_stream import drain_command_output
from app.services.runtime.sandbox.sandbox_spec import SandboxCreateSpec


# 当前课程针对已验证的 macOS Docker Desktop 环境。
# 路径和连接目标由服务端决定，不接受模型传入。
DOCKER_EXECUTABLE = (
    "/Applications/Docker.app/Contents/Resources/bin/docker"
)
DOCKER_HOST = f"unix://{Path.home()}/.docker/run/docker.sock"

DOCKER_CLIENT_TIMEOUT_SECONDS = 10
DOCKER_PIPE_BUFFER_BYTES = 4096

DockerClientErrorCode = Literal[
    "docker_client_unavailable",
    "docker_client_timeout",
    "docker_client_failed",
    "docker_response_unusable",
]


class DockerClientError(RuntimeError):
    """提供固定错误分类，不向调用方返回原始 stderr 或系统异常。"""

    def __init__(self, code: DockerClientErrorCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DockerClientResult:
    """客户端已经退出、两路输出已经排空后的内部结果。"""

    return_code: int
    streams: CapturedCommandStreams


async def _collect_client(
    process: asyncio.subprocess.Process,
) -> DockerClientResult:
    """读到两路 EOF 后，继续确认客户端进程已经退出。"""

    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Docker 客户端输出管道未建立")

    streams = await drain_command_streams(
        stdout=process.stdout,
        stderr=process.stderr,
    )
    return_code = await process.wait()

    return DockerClientResult(
        return_code=return_code,
        streams=streams,
    )


async def _finish_client(
    spawn_task: asyncio.Task[asyncio.subprocess.Process],
    collect_task: asyncio.Task[DockerClientResult] | None,
) -> None:
    """取得进程句柄、停止仍运行的客户端，并回收所有读取任务。"""

    try:
        # 启动期间发生取消时，仍需取得启动结果。
        # 否则可能已经创建进程，却丢失了负责回收它的句柄。
        process = await spawn_task
    except Exception:  # noqa: BLE001 -- 接收启动失败；原异常由调用层脱敏传播。
        # 启动任务失败时没有可供本层回收的进程句柄。
        # 原始启动错误由调用层转换为固定错误。
        return

    if process.returncode is None:
        try:
            # 这里只停止本次拥有的 Docker CLI 进程。
            # 不能将其解释为 daemon 已撤销容器操作。
            process.kill()
        except ProcessLookupError:
            # 检查与发送信号之间，进程可能已经退出。
            pass

    if collect_task is not None:
        # 不取消读取任务：进程退出时仍需排空管道。
        # 同时取走任务异常，避免留下无人接收的异常。
        await asyncio.gather(
            collect_task,
            return_exceptions=True,
        )

    # 启动阶段就超时，可能还没有 collect_task；
    # 捕获阶段失败，也可能留下尚未读完的管道。
    # 原读取任务结束后才接管，避免两个读取者争抢同一个流。
    readers = [
        reader
        for reader in (process.stdout, process.stderr)
        if reader is not None
    ]
    await asyncio.gather(
        *(drain_command_output(reader) for reader in readers),
        return_exceptions=True,
    )

    # 发送 kill 不是回收完成；必须等待操作系统报告退出。
    await process.wait()


async def _wait_for_cleanup(
    cleanup_task: asyncio.Task[None],
) -> None:
    """重复取消只延迟到收尾完成，不能中断已开始的资源回收。"""

    cancellation_requested = False

    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancellation_requested = True

    # 接收收尾任务自身的结果，不能把清理失败当作清理成功。
    cleanup_task.result()

    if cancellation_requested:
        raise asyncio.CancelledError


async def _run_docker_client(
    arguments: tuple[str, ...],
) -> DockerClientResult:
    """服务端内部调用底座；不得直接接收模型提供的 Docker 参数。"""

    # 不继承 DOCKER_HOST、DOCKER_CONTEXT 等宿主环境覆盖项。
    # 宿主客户端环境与容器内命令环境是两个独立边界。
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(Path.home()),
        "LANG": "C",
        "LC_ALL": "C",
    }

    spawn_task = asyncio.create_task(
        asyncio.create_subprocess_exec(
            DOCKER_EXECUTABLE,
            f"--host={DOCKER_HOST}",
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/",
            env=environment,
            limit=DOCKER_PIPE_BUFFER_BYTES,
        ),
        name="docker-client-spawn",
    )
    collect_task: asyncio.Task[DockerClientResult] | None = None

    try:
        try:
            # 超时覆盖启动、输出读取与等待进程退出。
            # shield 保留资源所有权，收尾阶段统一处理这些任务。
            async with asyncio.timeout(
                DOCKER_CLIENT_TIMEOUT_SECONDS
            ):
                process = await asyncio.shield(spawn_task)
                collect_task = asyncio.create_task(
                    _collect_client(process),
                    name="docker-client-collect",
                )
                result = await asyncio.shield(collect_task)
        except TimeoutError:
            raise DockerClientError(
                "docker_client_timeout"
            ) from None
        except OSError:
            raise DockerClientError(
                "docker_client_unavailable"
            ) from None
        except Exception:  # noqa: BLE001 -- 外部进程边界统一脱敏；不捕获取消。
            raise DockerClientError(
                "docker_client_failed"
            ) from None

        if result.return_code != 0:
            raise DockerClientError("docker_client_failed")

        # 结构化响应不能拿截断后的前缀继续解析。
        # 输出缓冲使用 UTF-8 replacement 解码；这里保守拒绝替换字符，
        # 即使它原本就是合法文本，也要求上层将响应视为不可确认。
        streams = result.streams
        if (
            streams.stdout.truncated
            or streams.stderr.truncated
            or "\ufffd" in streams.stdout.text
        ):
            raise DockerClientError("docker_response_unusable")

        return result
    finally:
        # finally 中不返回结果，也不吞掉调用者的取消异常。
        # 正常、失败、超时和取消都经过同一条收尾路径。
        cleanup_task = asyncio.create_task(
            _finish_client(spawn_task, collect_task),
            name="docker-client-cleanup",
        )
        await _wait_for_cleanup(cleanup_task)


async def inspect_sandbox_container(
    *,
    execution_token: str,
) -> str:
    """按服务端执行标识读取容器 JSON，身份确认由上层继续完成。"""

    if (
        not isinstance(execution_token, str)
        or re.fullmatch(r"[0-9a-f]{32}", execution_token) is None
    ):
        raise ValueError("execution_token 必须是 32 位小写十六进制")

    # 调用方只能提供受限标识，不能追加 Docker 选项或改变操作类型。
    container_name = f"agent-sandbox-{execution_token}"
    result = await _run_docker_client(
        ("container", "inspect", container_name)
    )

    return result.streams.stdout.text


async def create_sandbox_container(
    *,
    spec: SandboxCreateSpec,
) -> str:
    """执行服务端生成的创建规格，返回尚未解析的创建响应。"""

    # 此接口仅供服务端编排层调用。
    # 类型检查不是策略验证：spec 必须来自可信构造函数，
    # 不能把外部 JSON 或模型输出直接转换为这个对象后传入。
    if not isinstance(spec, SandboxCreateSpec):
        raise TypeError("spec 必须是服务端生成的 SandboxCreateSpec")

    # 规格保留了完整命令形式，调用底座则自行选择可信可执行程序。
    # 检查前缀后移除 docker 占位项，不能执行 spec.argv[0]。
    if spec.argv[:2] != ("docker", "create"):
        raise ValueError("创建规格必须使用 docker create")

    result = await _run_docker_client(spec.argv[1:])

    return result.streams.stdout.text


def _validate_full_container_id(container_id: str) -> None:
    """删除/删除后查询必须使用完整 ID，不接受名称、短 ID 或选项。"""

    if not isinstance(container_id, str) or re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise ValueError("container_id 必须是完整小写容器 ID")


async def remove_sandbox_container(*, container_id: str) -> None:
    """内部删除适配器；调用方必须先完成身份核对，不使用强制删除。"""

    _validate_full_container_id(container_id)
    result = await _run_docker_client(("container", "rm", container_id))
    # 成功退出还需核对响应，不能将其他目标的回执当成本次成功。
    if result.streams.stdout.text not in (container_id, container_id + "\n", container_id + "\r\n"):
        raise DockerClientError("docker_response_unusable")


async def is_sandbox_container_absent(*, container_id: str) -> bool:
    """成功列举全部状态后判断目标是否缺失；调用失败绝不解释为缺失。"""

    _validate_full_container_id(container_id)
    result = await _run_docker_client((
        "container", "ls", "--all", "--quiet", "--no-trunc",
        "--filter", f"id={container_id}",
    ))
    text = result.streams.stdout.text
    if text == "":
        return True
    if text in (container_id, container_id + "\n", container_id + "\r\n"):
        return False
    raise DockerClientError("docker_response_unusable")


async def inspect_sandbox_container_by_id(*, container_id: str) -> str:
    """运行阶段固定按完整ID查询，避免名称重用改变查询对象。"""

    _validate_full_container_id(container_id)
    result = await _run_docker_client(("container", "inspect", container_id))
    return result.streams.stdout.text


async def stop_sandbox_container(*, container_id: str) -> None:
    """请求停止并核对回执；不将回执本身当作停止状态证据。"""

    _validate_full_container_id(container_id)
    # 信号与宽限由服务端固定，不依赖镜像配置或模型参数。
    result = await _run_docker_client((
        "container", "stop", "--signal=SIGTERM", "--timeout=2", container_id,
    ))
    if result.streams.stdout.text not in (container_id, container_id + "\n", container_id + "\r\n"):
        raise DockerClientError("docker_response_unusable")


async def start_sandbox_container(*, container_id: str) -> None:
    """只启动已由上层复核的完整 ID；回执不代替运行状态查询。"""

    _validate_full_container_id(container_id)
    result = await _run_docker_client(("container", "start", container_id))
    if result.streams.stdout.text not in (container_id, container_id + "\n", container_id + "\r\n"):
        raise DockerClientError("docker_response_unusable")
