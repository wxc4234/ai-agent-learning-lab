"""异步排空 Docker 非 TTY attach 字节流，不拥有连接或容器。"""

import asyncio

from app.services.runtime.command.command_capture import CapturedCommandStreams
from app.services.runtime.docker.docker_attach_parser import (
    MAX_ATTACH_CHUNK_BYTES,
    DockerAttachParser,
)


async def drain_docker_attach(
    reader: asyncio.StreamReader,
) -> CapturedCommandStreams:
    """正常读到 EOF 且帧完整时返回输出；失败和取消向上传播。"""

    # 每次调用独立创建解析器，避免不同命令共享帧位置或输出额度。
    # 同一条流必须只有一个读取者，不能并发调用本函数消费同一个 reader。
    parser = DockerAttachParser()

    while True:
        # 固定读取上限，不使用无参数 read() 累计整条输出。
        # 短读取是合法情况，不能据此判断连接已经结束。
        chunk = await reader.read(MAX_ATTACH_CHUNK_BYTES)

        # 必须先校验类型，再判断 EOF。
        # None 或空 bytearray 都属于适配器错误，不能视为正常结束。
        if not isinstance(chunk, bytes):
            raise TypeError("attach 输出流必须返回 bytes")

        # 校验读取适配器是否遵守 read(n) 契约。
        # 此检查无法撤销适配器已经分配的内存，不构成内存隔离。
        if len(chunk) > MAX_ATTACH_CHUNK_BYTES:
            raise ValueError("attach 输出流返回的数据超过单次读取上限")

        if chunk == b"":
            # 只有正常 EOF 才执行完成检查。
            # 半个帧头或未读完的正文会由解析器拒绝。
            # 不放在 finally 中，避免把失败或取消包装成成功。
            return parser.finish()

        # 解析器负责通道拆分和独立捕获限额。
        # 即使保存额度已经耗尽，也必须继续消费后续帧。
        # 协议错误直接传播，本层不重试，也不继续读取。
        parser.feed(chunk)

        # read() 在缓冲区有数据时可能立即返回。
        # 每块处理后主动让出执行权，使持续输出期间仍可响应取消。
        await asyncio.sleep(0)
