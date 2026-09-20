"""命令输出的有界字节捕获；不读取管道或启动进程。"""

from dataclasses import dataclass

from app.services.runtime.command.command_contracts import (
    MAX_CAPTURE_BYTES_PER_STREAM,
    MAX_OUTPUT_CHARACTERS_PER_STREAM,
)


@dataclass(frozen=True, slots=True)
class CapturedCommandOutput:
    """单路输出的最终快照。"""

    text: str

    # 表示实际保存的原始字节数，不是进程输出总量。
    captured_byte_count: int

    # 原始字节截断与展示字符截断分别保留，便于定位限制来源。
    capture_truncated: bool
    display_truncated: bool

    @property
    def truncated(self) -> bool:
        """转换为 CommandResult 中的单路截断标记。"""
        return self.capture_truncated or self.display_truncated


class CommandOutputBuffer:
    """由单个读取者使用的单路输出缓冲，不提供线程同步。"""

    __slots__ = (
        "_buffer",
        "_capture_truncated",
        "_finished",
        "_max_capture_bytes",
        "_max_output_characters",
    )

    def __init__(
        self,
        *,
        max_capture_bytes: int = MAX_CAPTURE_BYTES_PER_STREAM,
        max_output_characters: int = MAX_OUTPUT_CHARACTERS_PER_STREAM,
    ) -> None:
        # 这些参数只供服务端调用，不能加入模型请求。
        # 允许收紧额度和使用零额度，但不允许扩大服务端硬上限。
        limits = (
            (
                max_capture_bytes,
                MAX_CAPTURE_BYTES_PER_STREAM,
                "max_capture_bytes",
            ),
            (
                max_output_characters,
                MAX_OUTPUT_CHARACTERS_PER_STREAM,
                "max_output_characters",
            ),
        )

        for value, upper_bound, name in limits:
            # bool 是 int 的子类，这里明确拒绝 True/False。
            if type(value) is not int:
                raise TypeError(f"{name} 必须是整数")

            if not 0 <= value <= upper_bound:
                raise ValueError(f"{name} 超出服务端允许范围")

        self._max_capture_bytes = max_capture_bytes
        self._max_output_characters = max_output_characters
        self._buffer = bytearray()
        self._capture_truncated = False
        self._finished: CapturedCommandOutput | None = None

    @property
    def captured_byte_count(self) -> int:
        """返回当前保存量，不能据此推算完整输出大小。"""
        return len(self._buffer)

    def feed(self, chunk: bytes) -> None:
        """接收一个字节块，只保存额度内的前缀。"""

        # 完成后拒绝继续写入，避免快照与缓冲内容不一致。
        if self._finished is not None:
            raise RuntimeError("输出缓冲已完成，不能继续接收数据")

        if not isinstance(chunk, bytes):
            raise TypeError("输出块必须是 bytes")

        remaining = self._max_capture_bytes - len(self._buffer)

        if len(chunk) > remaining:
            # 标记一旦为真就保留；后续空块不能抹掉截断事实。
            self._capture_truncated = True

        if remaining > 0:
            # 切片长度受剩余额度限制，不复制整个超大输入块。
            self._buffer.extend(chunk[:remaining])

        # 即使额度已经用完也正常返回。
        # 调用方仍需继续读取管道，只是不再保存后续内容。

    def finish(self) -> CapturedCommandOutput:
        """结束捕获并生成快照；重复调用返回同一个结果。"""

        if self._finished is not None:
            return self._finished

        # 所有已保存字节统一解码，避免分块边界拆断 UTF-8 字符。
        # 非法字节或捕获末尾的不完整字符使用替换字符 U+FFFD。
        decoded = self._buffer.decode("utf-8", errors="replace")

        display_truncated = (
            len(decoded) > self._max_output_characters
        )

        self._finished = CapturedCommandOutput(
            text=decoded[:self._max_output_characters],
            captured_byte_count=len(self._buffer),
            capture_truncated=self._capture_truncated,
            display_truncated=display_truncated,
        )
        return self._finished
