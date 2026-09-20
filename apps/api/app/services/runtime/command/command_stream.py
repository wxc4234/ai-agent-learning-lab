"""分块排空单路命令输出；不启动、终止或回收进程。"""

import asyncio

from app.services.runtime.command.command_contracts import (
    MAX_CAPTURE_BYTES_PER_STREAM,
    MAX_OUTPUT_CHARACTERS_PER_STREAM,
)
from app.services.runtime.command.command_output import (
    CapturedCommandOutput,
    CommandOutputBuffer,
)


# 固定正数读取大小，避免 read() 默认读取到 EOF 并累计完整输出。
COMMAND_OUTPUT_READ_BYTES = 4096


async def drain_command_output(
    reader: asyncio.StreamReader,
    *,
    max_capture_bytes: int = MAX_CAPTURE_BYTES_PER_STREAM,
    max_output_characters: int = MAX_OUTPUT_CHARACTERS_PER_STREAM,
) -> CapturedCommandOutput:
    """读取到 EOF，只保留有限输出；异常和取消交给调用方处理。"""

    # 先验证服务端额度，再开始消费流。
    # 每次调用创建独立缓冲，stdout 和 stderr 不共享额度或状态。
    buffer = CommandOutputBuffer(
        max_capture_bytes=max_capture_bytes,
        max_output_characters=max_output_characters,
    )

    while True:
        # read(n) 可以返回少于 n 个字节，不能把短块当成 EOF。
        # 这里不捕获异常：读取失败与 CancelledError 都向上传播。
        chunk = await reader.read(COMMAND_OUTPUT_READ_BYTES)

        # 在判断 EOF 前检查类型，避免错误的读取实现返回 None
        # 或空 bytearray 时被当成正常结束。
        if not isinstance(chunk, bytes):
            raise TypeError("输出流必须返回 bytes")

        # StreamReader.read(n) 应保证最多返回 n 字节。
        # 这个检查用于发现不符合约定的适配器，不是内存隔离机制。
        if len(chunk) > COMMAND_OUTPUT_READ_BYTES:
            raise ValueError("输出流返回的数据超过单次读取上限")

        if chunk == b"":
            # 只有正常读到 EOF 才生成成功快照。
            # 不放在 finally 中，避免把取消或读取失败转成成功。
            return buffer.finish()

        # 达到捕获上限后仍继续读取，由缓冲丢弃超出部分。
        # 不能根据 captured_byte_count 或 truncated 提前退出循环。
        buffer.feed(chunk)

        # reader 内部已有数据时，read 可能立即返回而不挂起。
        # 主动让出执行权，使持续输出期间其他任务和取消能被处理。
        await asyncio.sleep(0)
