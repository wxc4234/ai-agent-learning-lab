"""增量解析 Docker 非 TTY attach 输出，不连接 daemon。"""

from typing import NoReturn

from app.services.runtime.command.command_capture import CapturedCommandStreams
from app.services.runtime.command.command_output import CommandOutputBuffer


ATTACH_HEADER_BYTES = 8

# 调用方应使用固定大小读取，不把无限增长的输入块交给同步解析器。
MAX_ATTACH_CHUNK_BYTES = 4096

# 项目接收策略，不是 Docker 协议规定的最大值。
# 解析时不会按这个长度预先分配正文缓冲。
MAX_ATTACH_FRAME_BYTES = 1024 * 1024


class DockerAttachProtocolError(ValueError):
    """帧格式或结束位置不符合当前接收策略。"""

    def __init__(self) -> None:
        # 不输出原始帧内容，避免将命令数据或daemon错误泄漏到异常中。
        super().__init__("无法确认 Docker attach 输出帧完整有效")


class DockerAttachParser:
    """由单个读取者使用，分别保存 stdout/stderr 的有界前缀。"""

    __slots__ = (
        "_failed",
        "_finished",
        "_header",
        "_remaining",
        "_stderr",
        "_stdout",
        "_stream",
    )

    def __init__(self) -> None:
        self._failed = False
        self._finished: CapturedCommandStreams | None = None

        # 只缓存尚未解析的8字节头部，不累计完整网络输入。
        self._header = bytearray()

        # remaining为当前帧尚未接收的正文长度。
        # 为0时，下一个字节属于新帧头。
        self._remaining = 0
        self._stream = 0

        self._stdout = CommandOutputBuffer()
        self._stderr = CommandOutputBuffer()

    def _fail(self) -> NoReturn:
        """协议错误使本实例进入不可恢复的失败状态。"""

        self._failed = True
        raise DockerAttachProtocolError()

    def feed(self, chunk: bytes) -> None:
        """消费一个有界字节块；空块不表示EOF。"""

        if self._failed:
            raise DockerAttachProtocolError()

        if self._finished is not None:
            raise RuntimeError("attach 解析已完成，不能继续接收数据")

        # 参数误用在消费数据之前拒绝，不改变已有解析位置。
        if not isinstance(chunk, bytes):
            raise TypeError("attach 输入块必须是 bytes")

        if len(chunk) > MAX_ATTACH_CHUNK_BYTES:
            raise ValueError("attach 输入块超过单次读取上限")

        position = 0

        while position < len(chunk):
            if self._remaining == 0:
                needed = ATTACH_HEADER_BYTES - len(self._header)
                take = min(needed, len(chunk) - position)

                self._header.extend(chunk[position:position + take])
                position += take

                if len(self._header) < ATTACH_HEADER_BYTES:
                    # 等待后续feed补齐头部，不把短读取误认为结束。
                    continue

                stream = self._header[0]
                reserved = self._header[1:4]
                frame_size = int.from_bytes(
                    self._header[4:8],
                    byteorder="big",
                )

                if (
                    stream not in (1, 2)
                    or reserved != b"\x00\x00\x00"
                    or frame_size > MAX_ATTACH_FRAME_BYTES
                ):
                    self._fail()

                self._header.clear()
                self._stream = stream
                self._remaining = frame_size

                # 空正文帧合法；头部已经消费，不会形成死循环。
                # 非空帧则在下一轮进入正文分支。
                continue

            take = min(self._remaining, len(chunk) - position)
            payload = chunk[position:position + take]

            # 通道编号已在头部检查；正文不能被当作下一帧头。
            buffer = self._stdout if self._stream == 1 else self._stderr
            buffer.feed(payload)

            position += take
            self._remaining -= take

            # 即使buffer已达到保存上限，也继续消费帧正文，
            # 否则后续帧会错位，底层连接也可能因不读取而阻塞。

    def finish(self) -> CapturedCommandStreams:
        """仅在完整帧边界结束；重复完成返回同一个快照。"""

        if self._failed:
            raise DockerAttachProtocolError()

        if self._finished is not None:
            return self._finished

        if self._header or self._remaining != 0:
            # 不能将半帧前缀包装成“完整采集成功”。
            self._fail()

        self._finished = CapturedCommandStreams(
            stdout=self._stdout.finish(),
            stderr=self._stderr.finish(),
        )
        return self._finished
