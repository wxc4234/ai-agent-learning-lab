"""attach 异步读取边界：内存流和受控读取器，不连接 Docker。"""

import asyncio

import pytest

from app.services.runtime.docker.docker_attach_parser import DockerAttachProtocolError
from app.services.runtime.docker.docker_attach_stream import drain_docker_attach
from tests.runtime.docker.test_docker_attach_parser import frame


class ScriptedReader:
    """读取次数严格受脚本限制，意外多读立即失败。"""

    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.read_sizes = []

    async def read(self, size):
        self.read_sizes.append(size)
        item = next(self.chunks)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.mark.parametrize("size", [1, 2, 7, 8, 9, 4096])
def test_short_reads_cross_frame_utf8_and_interleaving(size):
    encoded = "你好🙂".encode()
    wire = frame(1, encoded[:1]) + frame(2, b"warn") + frame(1, encoded[1:]) + frame(2, b"")
    chunks = [wire[start:start + size] for start in range(0, len(wire), size)]
    reader = ScriptedReader(chunks + [b""])
    result = asyncio.run(drain_docker_attach(reader))
    assert result.stdout.text == "你好🙂"
    assert result.stderr.text == "warn"
    assert not result.stdout.truncated and not result.stderr.truncated
    assert reader.read_sizes == [4096] * (len(chunks) + 1)


@pytest.mark.parametrize("size", [0, 4096, 65536, 65537, 1024 * 1024])
def test_real_memory_stream_drains_large_output_and_late_other_channel(size):
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(frame(1, b"x" * size) + frame(2, b"late"))
        reader.feed_eof()
        result = await drain_docker_attach(reader)
        assert reader.at_eof()
        assert result.stdout.text == "x" * min(size, 65536)
        assert result.stdout.captured_byte_count == min(size, 65536)
        assert result.stdout.capture_truncated is (size > 65536)
        assert result.stderr.text == "late" and not result.stderr.truncated
    asyncio.run(scenario())


def test_empty_eof_returns_without_another_read():
    reader = ScriptedReader([b"", AssertionError("must not read after EOF")])
    result = asyncio.run(drain_docker_attach(reader))
    assert result.stdout.text == result.stderr.text == ""
    assert reader.read_sizes == [4096]


@pytest.mark.parametrize("length", range(1, 11))
def test_partial_header_or_payload_at_eof_is_failure(length):
    reader = ScriptedReader([frame(1, b"abc")[:length], b""])
    with pytest.raises(DockerAttachProtocolError):
        asyncio.run(drain_docker_attach(reader))
    assert reader.read_sizes == [4096, 4096]


@pytest.mark.parametrize("chunk,error", [
    (None, TypeError), ("", TypeError), (bytearray(), TypeError),
    (memoryview(b""), TypeError), (False, TypeError),
    (b"x" * 4097, ValueError),
])
def test_invalid_read_contract_is_not_success(chunk, error):
    reader = ScriptedReader([frame(1, b"prior"), chunk])
    with pytest.raises(error):
        asyncio.run(drain_docker_attach(reader))
    assert reader.read_sizes == [4096, 4096]


@pytest.mark.parametrize("prefix", [frame(1, b"prior"), frame(1, b"unfinished")[:9]])
@pytest.mark.parametrize("error", [OSError("read failed"), RuntimeError("adapter failed"), asyncio.CancelledError()])
def test_read_failure_propagates_original_exception(prefix, error):
    reader = ScriptedReader([prefix, error])
    with pytest.raises(type(error)) as caught:
        asyncio.run(drain_docker_attach(reader))
    assert caught.value is error
    assert reader.read_sizes == [4096, 4096]


def test_protocol_failure_does_not_read_again():
    reader = ScriptedReader([frame(1, b"prior"), frame(3, b"invalid"), b""])
    with pytest.raises(DockerAttachProtocolError):
        asyncio.run(drain_docker_attach(reader))
    assert reader.read_sizes == [4096, 4096]


def test_delayed_payload_and_eof_are_required():
    async def scenario():
        reader = asyncio.StreamReader()
        task = asyncio.create_task(drain_docker_attach(reader))
        try:
            await asyncio.sleep(0)
            assert not task.done()
            wire = frame(1, b"hello")
            reader.feed_data(wire[:9])
            await asyncio.sleep(0)
            assert not task.done()
            reader.feed_data(wire[9:])
            await asyncio.sleep(0)
            assert not task.done(), "complete frame alone is not EOF"
            reader.feed_eof()
            result = await asyncio.wait_for(task, 1)
            assert result.stdout.text == "hello"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", [b"", frame(1, b"done"), frame(1, b"partial")[:9]])
def test_cancellation_while_waiting_preserves_reader_ownership(prefix):
    async def scenario():
        entered = asyncio.Event()

        class WaitingReader(asyncio.StreamReader):
            async def read(self, n=-1):
                if not self._buffer:
                    entered.set()
                return await super().read(n)

        reader = WaitingReader()
        reader.feed_data(prefix)
        task = asyncio.create_task(drain_docker_attach(reader))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # 取消读取不意味着拥有者的流被关闭或主动读到 EOF。
            reader.feed_data(b"still usable")
            reader.feed_eof()
            assert await reader.read() == b"still usable"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_immediate_reads_still_allow_cancellation_after_capture_exhaustion():
    async def scenario():
        exhausted = asyncio.Event()

        class ImmediateReader:
            calls = 0

            async def read(self, size):
                assert size == 4096
                self.calls += 1
                if self.calls == 20:
                    exhausted.set()
                # 有限上界让缺少调度点的错误实现失败，而不是挂住测试。
                return frame(1, b"x" * 4088) if self.calls <= 100 else b""

        reader = ImmediateReader()
        task = asyncio.create_task(drain_docker_attach(reader))
        try:
            await asyncio.wait_for(exhausted.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert 20 <= reader.calls < 100
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_concurrent_calls_have_independent_parser_state():
    async def scenario():
        first = ScriptedReader([frame(1, b"one")[:4], frame(1, b"one")[4:], b""])
        second = ScriptedReader([frame(2, b"two"), b""])
        a, b = await asyncio.gather(drain_docker_attach(first), drain_docker_attach(second))
        assert a.stdout.text == "one" and a.stderr.text == ""
        assert b.stdout.text == "" and b.stderr.text == "two"
    asyncio.run(scenario())
