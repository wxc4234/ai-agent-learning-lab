"""单路异步排空的EOF、读取契约及取消边界，不启动外部命令。"""

import asyncio

import pytest

from app.services.runtime.command.command_stream import drain_command_output


class ScriptedReader:
    """显式记录块大小，并在意外多读时失败。"""

    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.read_sizes = []

    async def read(self, size):
        self.read_sizes.append(size)
        item = next(self.chunks)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.mark.parametrize("data", [b"", b"a", b"a" * 4096, b"a" * 4097, b"x" * 70_000, "中文🙂".encode()])
def test_real_memory_stream_reaches_eof(data):
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        result = await drain_command_output(reader)
        assert reader.at_eof()
        assert result.text == data[:65_536].decode("utf-8", errors="replace")
        assert result.captured_byte_count == min(len(data), 65_536)
        assert result.capture_truncated is (len(data) > 65_536)
    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, 2, 6])
def test_short_chunks_and_capture_exhaustion_do_not_end_reading(limit):
    data = "你好".encode()
    reader = ScriptedReader([bytes([byte]) for byte in data] + [b""])
    result = asyncio.run(drain_command_output(reader, max_capture_bytes=limit))
    assert reader.read_sizes == [4096] * 7
    assert result.text == data[:limit].decode("utf-8", errors="replace")
    assert result.capture_truncated is (limit < len(data))


def test_display_limit_does_not_end_reading():
    reader = ScriptedReader([b"abc", b"def", b""])
    result = asyncio.run(drain_command_output(reader, max_output_characters=1))
    assert result.text == "a"
    assert result.display_truncated and not result.capture_truncated
    assert reader.read_sizes == [4096] * 3


@pytest.mark.parametrize("field", ["max_capture_bytes", "max_output_characters"])
@pytest.mark.parametrize("value,error", [(True, TypeError), (-1, ValueError), (65_537, ValueError)])
def test_invalid_policy_fails_before_consuming_stream(field, value, error):
    reader = ScriptedReader([])
    with pytest.raises(error):
        asyncio.run(drain_command_output(reader, **{field: value}))
    assert reader.read_sizes == []


@pytest.mark.parametrize("chunk,error", [
    (None, TypeError), ("", TypeError), (bytearray(), TypeError),
    (memoryview(b"a"), TypeError), (b"x" * 4097, ValueError),
])
def test_invalid_read_result_is_not_eof(chunk, error):
    reader = ScriptedReader([chunk])
    with pytest.raises(error):
        asyncio.run(drain_command_output(reader))
    assert reader.read_sizes == [4096]


@pytest.mark.parametrize("error", [OSError("read failed"), RuntimeError("adapter failed")])
@pytest.mark.parametrize("limit", [0, 65_536])
def test_read_failure_after_partial_output_propagates_unchanged(error, limit):
    reader = ScriptedReader([b"partial", error])
    with pytest.raises(type(error)) as caught:
        asyncio.run(drain_command_output(reader, max_capture_bytes=limit))
    assert caught.value is error
    assert reader.read_sizes == [4096, 4096]


def test_empty_read_is_eof_without_an_extra_read():
    reader = ScriptedReader([b"", RuntimeError("must not read again")])
    assert asyncio.run(drain_command_output(reader)).text == ""
    assert reader.read_sizes == [4096]


def test_waits_for_delayed_data_and_eof():
    async def scenario():
        reader = asyncio.StreamReader()
        task = asyncio.create_task(drain_command_output(reader))
        try:
            await asyncio.sleep(0)
            assert not task.done()
            reader.feed_data(b"short")
            await asyncio.sleep(0)
            assert not task.done(), "a short chunk must not be treated as EOF"
            reader.feed_data(b" tail")
            reader.feed_eof()
            result = await asyncio.wait_for(task, 1)
            assert result.text == "short tail"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", [b"", b"partial"])
def test_cancellation_while_waiting_for_read_is_not_success(prefix):
    async def scenario():
        entered = asyncio.Event()

        class WaitingReader(asyncio.StreamReader):
            async def read(self, n=-1):
                if not self._buffer:
                    entered.set()
                return await super().read(n)

        reader = WaitingReader()
        reader.feed_data(prefix)
        task = asyncio.create_task(drain_command_output(reader))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # 读取函数不拥有流，也不会在取消时主动关闭它。
            reader.feed_data(b"still usable")
            reader.feed_eof()
            assert await reader.read() == b"still usable"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_immediate_reads_yield_to_cancellation_after_budget_exhaustion():
    async def scenario():
        started = asyncio.Event()

        class ImmediateReader:
            calls = 0

            async def read(self, size):
                assert size == 4096
                self.calls += 1
                started.set()
                # 有限上界使缺少主动让出的错误实现快速失败，而非挂住测试。
                return b"x" if self.calls <= 1000 else b""

        reader = ImmediateReader()
        task = asyncio.create_task(drain_command_output(reader, max_capture_bytes=0))
        try:
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert reader.calls < 1000
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_concurrent_streams_have_independent_state():
    async def scenario():
        first = ScriptedReader([b"abc", b"def", b""])
        second = ScriptedReader(["中文".encode(), b""])
        a, b = await asyncio.gather(
            drain_command_output(first, max_capture_bytes=2),
            drain_command_output(second),
        )
        assert a.text == "ab" and a.truncated
        assert b.text == "中文" and not b.truncated
        assert first.read_sizes == [4096] * 3
        assert second.read_sizes == [4096] * 2
    asyncio.run(scenario())
