"""持有本机 attach 连接；不授权、启动、停止或删除容器。"""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.services.runtime.docker.docker_attach_http import (
    build_docker_attach_request,
    read_docker_attach_upgrade,
)


# 与现有 CLI 客户端使用同一本机 Docker Desktop socket；不读取 DOCKER_HOST。
DOCKER_ATTACH_SOCKET = Path.home() / ".docker/run/docker.sock"
ATTACH_CONNECT_TIMEOUT_SECONDS = 10
ATTACH_CLOSE_TIMEOUT_SECONDS = 2
ATTACH_READER_LIMIT_BYTES = 4096


class DockerAttachConnectionError(RuntimeError):
    """连接/发送/握手失败，不公开路径或服务端原文。"""

    def __init__(self) -> None:
        super().__init__("无法建立 Docker attach 连接")


class DockerAttachCloseError(RuntimeError):
    """已请求强制关闭本地传输，但优雅关闭未确认。"""

    def __init__(self) -> None:
        super().__init__("Docker attach 连接关闭未确认")


async def _close_writer(writer: asyncio.StreamWriter) -> bool:
    """有界等待优雅关闭；失败时中止本地传输，不重试网络请求。"""

    try:
        writer.close()
        async with asyncio.timeout(ATTACH_CLOSE_TIMEOUT_SECONDS):
            await writer.wait_closed()
        return True
    except Exception:  # noqa: BLE001 -- 仅返回关闭证据，不泄漏底层错误。
        writer.transport.abort()
        return False


async def _settle_writer(writer: asyncio.StreamWriter) -> tuple[bool, bool]:
    """拥有并回收唯一关闭任务；重复取消不能使其成为后台遗留任务。"""

    task = asyncio.create_task(_close_writer(writer), name="docker-attach-close")
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


@asynccontextmanager
async def open_docker_attach(
    *, container_id: str,
) -> AsyncIterator[asyncio.StreamReader]:
    """借出握手后的读取端；调用方必须先完成目标归属及非TTY策略核对。"""

    # 无效目标在分配 socket 前拒绝；本函数不是资源授权入口。
    request = build_docker_attach_request(container_id=container_id)
    owned_socket: socket.socket | None = None
    writer: asyncio.StreamWriter | None = None
    failure: BaseException | None = None

    try:
        try:
            # 总预算覆盖 connect、流装配、write/drain 与响应握手。
            # 退出预算后才 yield，命令输出寿命不受握手预算限制。
            async with asyncio.timeout(ATTACH_CONNECT_TIMEOUT_SECONDS):
                owned_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                owned_socket.setblocking(False)
                await asyncio.get_running_loop().sock_connect(owned_socket, str(DOCKER_ATTACH_SOCKET))
                reader, writer = await asyncio.open_unix_connection(
                    sock=owned_socket, limit=ATTACH_READER_LIMIT_BYTES,
                )
                # 返回 writer 后由传输拥有 socket；此前任一步失败由 finally 兜底关闭。
                owned_socket = None
                writer.write(request)
                await writer.drain()
                await read_docker_attach_upgrade(reader)
        except Exception:  # noqa: BLE001 -- 仅封装连接阶段，不能吞掉调用方业务异常。
            raise DockerAttachConnectionError() from None

        yield reader
    except BaseException as error:
        # 保存原始异常，使关闭失败不会掩盖业务失败或取消。
        failure = error
        raise
    finally:
        if owned_socket is not None:
            owned_socket.close()
        if writer is not None:
            closed, cancelled = await _settle_writer(writer)
            if not closed and failure is not None:
                failure.add_note("Docker attach 连接关闭未确认；已请求中止本地传输")
            if cancelled and not isinstance(failure, asyncio.CancelledError):
                error = asyncio.CancelledError()
                if not closed:
                    error.add_note("Docker attach 连接关闭未确认；已请求中止本地传输")
                raise error
            if not closed and failure is None:
                raise DockerAttachCloseError()
