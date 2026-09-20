"""输出缓冲的纯内存边界测试，不启动进程或读取真实管道。"""

from dataclasses import FrozenInstanceError

import pytest

from app.services.runtime.command_contracts import CommandResult
from app.services.runtime.command_output import CommandOutputBuffer


@pytest.mark.parametrize("field", ["max_capture_bytes", "max_output_characters"])
@pytest.mark.parametrize("value", [None, True, False, "1", 1.0])
def test_limit_type_is_strict(field, value):
    with pytest.raises(TypeError):
        CommandOutputBuffer(**{field: value})


@pytest.mark.parametrize("field", ["max_capture_bytes", "max_output_characters"])
@pytest.mark.parametrize("value", [-1, 65_537])
def test_limits_cannot_exceed_server_policy(field, value):
    with pytest.raises(ValueError):
        CommandOutputBuffer(**{field: value})


@pytest.mark.parametrize("limit", [0, 1, 65_536])
def test_empty_output_is_not_truncated(limit):
    buffer = CommandOutputBuffer(max_capture_bytes=limit, max_output_characters=limit)
    buffer.feed(b"")
    result = buffer.finish()
    assert result.text == ""
    assert result.captured_byte_count == buffer.captured_byte_count == 0
    assert not result.truncated


@pytest.mark.parametrize("size", [65_535, 65_536, 65_537])
def test_default_byte_boundary(size):
    buffer = CommandOutputBuffer()
    buffer.feed(b"a" * size)
    result = buffer.finish()
    assert result.text == "a" * min(size, 65_536)
    assert result.captured_byte_count == min(size, 65_536)
    assert result.capture_truncated is (size > 65_536)
    assert not result.display_truncated


def test_full_buffer_discards_future_chunks_without_losing_prefix():
    buffer = CommandOutputBuffer(max_capture_bytes=5)
    buffer.feed(b"he")
    buffer.feed(b"llo")
    buffer.feed(b"")
    # 大块及持续后续数据均不能使保存量继续增长。
    for chunk in [b"x" * 1_000_000, *([b"extra"] * 100), b""]:
        buffer.feed(chunk)
        assert buffer.captured_byte_count == 5
    result = buffer.finish()
    assert result.text == "hello"
    assert result.capture_truncated
    assert not result.display_truncated


@pytest.mark.parametrize("data,expected", [
    ("你好🙂e\u0301".encode(), "你好🙂e\u0301"),
    (b"a\xffb", "a\ufffdb"),
    (b"\xe4\xbd", "\ufffd"),
    (b"a\x00b\r\n", "a\x00b\r\n"),
])
def test_decoding_does_not_depend_on_chunk_boundaries(data, expected):
    # 遍历每个切分位置，包含多字节字符内部和非法序列边界。
    for split in range(len(data) + 1):
        buffer = CommandOutputBuffer()
        buffer.feed(data[:split])
        buffer.feed(data[split:])
        result = buffer.finish()
        assert result.text == expected
        assert result.captured_byte_count == len(data)
        assert not result.truncated
    buffer = CommandOutputBuffer()
    for byte in data:
        buffer.feed(bytes([byte]))
    assert buffer.finish().text == expected


@pytest.mark.parametrize("byte_limit,char_limit,text,capture,display", [
    (0, 10, "", True, False),
    (10, 0, "", False, True),
    (3, 10, "你", True, False),
    (2, 10, "\ufffd", True, False),
    (4, 10, "你\ufffd", True, False),
    (6, 1, "你", False, True),
    (4, 1, "你", True, True),
    (6, 2, "你好", False, False),
])
def test_byte_and_display_limits_are_independent(byte_limit, char_limit, text, capture, display):
    buffer = CommandOutputBuffer(max_capture_bytes=byte_limit, max_output_characters=char_limit)
    buffer.feed("你好".encode())
    result = buffer.finish()
    assert result.text == text
    assert result.captured_byte_count == min(byte_limit, 6)
    assert result.capture_truncated is capture
    assert result.display_truncated is display
    assert result.truncated is (capture or display)


@pytest.mark.parametrize("chunk", [None, "text", bytearray(b"a"), memoryview(b"a"), 1])
def test_invalid_chunk_does_not_corrupt_buffer(chunk):
    buffer = CommandOutputBuffer(max_capture_bytes=3)
    buffer.feed(b"a")
    with pytest.raises(TypeError):
        buffer.feed(chunk)
    buffer.feed(b"bc")
    result = buffer.finish()
    assert result.text == "abc"
    assert not result.truncated


@pytest.mark.parametrize("chunk", [b"", b"late"])
def test_finish_is_idempotent_and_rejects_late_writes(chunk):
    buffer = CommandOutputBuffer()
    buffer.feed(b"before")
    result = buffer.finish()
    assert buffer.finish() is result
    with pytest.raises(RuntimeError):
        buffer.feed(chunk)
    assert buffer.finish() is result
    assert result.text == "before"
    with pytest.raises(FrozenInstanceError):
        result.text = "changed"


def test_two_streams_keep_separate_budgets_and_map_to_contract():
    stdout = CommandOutputBuffer(max_capture_bytes=3)
    stderr = CommandOutputBuffer(max_output_characters=2)
    stdout.feed(b"abcd")
    stderr.feed("错误详情".encode())
    out = stdout.finish()
    # 结束一路不会关闭另一路。
    stderr.feed(b"!")
    err = stderr.finish()
    assert out.text == "abc" and err.text == "错误"
    assert out.capture_truncated and not out.display_truncated
    assert not err.capture_truncated and err.display_truncated
    result = CommandResult(
        status="exited", exit_code=1, duration_ms=0,
        stdout=out.text, stderr=err.text,
        stdout_truncated=out.truncated, stderr_truncated=err.truncated,
    )
    assert CommandResult.model_validate_json(result.model_dump_json()) == result
    assert result.stdout_truncated and result.stderr_truncated
