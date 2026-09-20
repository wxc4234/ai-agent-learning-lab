"""attach分帧纯字节测试，不调用Docker或打开网络连接。"""

import random
from dataclasses import FrozenInstanceError

import pytest

from app.services.runtime.docker_attach_parser import (
    DockerAttachParser, DockerAttachProtocolError,
    MAX_ATTACH_CHUNK_BYTES, MAX_ATTACH_FRAME_BYTES,
)


def frame(channel, body):
    return bytes([channel, 0, 0, 0]) + len(body).to_bytes(4, "big") + body


def feed_blocks(parser, wire, size=4096):
    for start in range(0, len(wire), size):
        parser.feed(wire[start:start + size])


WIRE = frame(1, "你好".encode()) + frame(2, b"err") + frame(1, b"done")


@pytest.mark.parametrize("split", range(len(WIRE) + 1))
def test_every_single_split(split):
    parser = DockerAttachParser()
    parser.feed(WIRE[:split])
    parser.feed(b"")
    parser.feed(WIRE[split:])
    result = parser.finish()
    assert result.stdout.text == "你好done"
    assert result.stderr.text == "err"
    assert not result.stdout.truncated and not result.stderr.truncated


@pytest.mark.parametrize("size", [1, 2, 3, 7, 8, 9, 17, 4096])
def test_multiple_frames_and_empty_frames(size):
    parser = DockerAttachParser()
    wire = frame(1, b"") + WIRE + frame(2, b"") + frame(2, b"late")
    feed_blocks(parser, wire, size)
    result = parser.finish()
    assert result.stdout.text == "你好done"
    assert result.stderr.text == "errlate"


def test_all_two_cut_positions_with_cross_frame_utf8():
    encoded = "你".encode()
    wire = frame(1, encoded[:1]) + frame(2, b"x") + frame(1, encoded[1:])
    for first in range(len(wire) + 1):
        for second in range(first, len(wire) + 1):
            parser = DockerAttachParser()
            for piece in (wire[:first], wire[first:second], wire[second:]):
                parser.feed(piece)
            result = parser.finish()
            assert result.stdout.text == "你" and result.stderr.text == "x"


@pytest.mark.parametrize("seed", range(8))
def test_deterministic_random_partition_and_channel_interleaving(seed):
    rng = random.Random(seed)
    wire, expected = bytearray(), {1: bytearray(), 2: bytearray()}
    for _ in range(100):
        channel = rng.choice((1, 2))
        body = rng.randbytes(rng.randrange(50))
        wire.extend(frame(channel, body))
        expected[channel].extend(body)
    parser = DockerAttachParser()
    position = 0
    while position < len(wire):
        take = rng.randrange(1, 100)
        parser.feed(bytes(wire[position:position + take]))
        position += take
    result = parser.finish()
    assert result.stdout.text == expected[1].decode("utf-8", errors="replace")
    assert result.stderr.text == expected[2].decode("utf-8", errors="replace")


@pytest.mark.parametrize("channel", [1, 2])
@pytest.mark.parametrize("size", [65536, 65537, MAX_ATTACH_FRAME_BYTES])
def test_capture_limit_does_not_stop_parsing(channel, size):
    parser = DockerAttachParser()
    parser.feed(bytes([channel, 0, 0, 0]) + size.to_bytes(4, "big"))
    # 分块生成正文，不在解析器中缓存整个大帧。
    remaining = size
    while remaining:
        count = min(4096, remaining)
        parser.feed(b"x" * count)
        remaining -= count
        assert len(parser._header) <= 8
        assert parser._stdout.captured_byte_count <= 65536
        assert parser._stderr.captured_byte_count <= 65536
    parser.feed(frame(3 - channel, b"after"))
    result = parser.finish()
    target, other = (result.stdout, result.stderr) if channel == 1 else (result.stderr, result.stdout)
    assert target.text == "x" * 65536
    assert target.capture_truncated is (size > 65536)
    assert target.captured_byte_count == 65536
    assert other.text == "after" and not other.truncated


@pytest.mark.parametrize("header", [
    bytes([channel, 0, 0, 0]) + (0).to_bytes(4, "big") for channel in (0, 3, 4, 255)
] + [
    b"\x01\x01\x00\x00\x00\x00\x00\x00",
    b"\x01\x00\x01\x00\x00\x00\x00\x00",
    b"\x01\x00\x00\x01\x00\x00\x00\x00",
    b"\x01\x00\x00\x00" + (MAX_ATTACH_FRAME_BYTES + 1).to_bytes(4, "big"),
    b"\x02\x00\x00\x00\xff\xff\xff\xff",
])
def test_invalid_header_permanent_failure(header):
    parser = DockerAttachParser()
    parser.feed(frame(1, b"PRIVATE prior output"))
    with pytest.raises(DockerAttachProtocolError) as caught:
        parser.feed(header)
    assert str(caught.value) == "无法确认 Docker attach 输出帧完整有效"
    for action in (lambda: parser.feed(b""), lambda: parser.feed(frame(1, b"repair")), parser.finish):
        with pytest.raises(DockerAttachProtocolError):
            action()


@pytest.mark.parametrize("length", range(1, 13))
def test_partial_header_or_payload_at_eof_rejected(length):
    wire = frame(1, b"hello")
    parser = DockerAttachParser()
    parser.feed(wire[:length])
    with pytest.raises(DockerAttachProtocolError):
        parser.finish()
    with pytest.raises(DockerAttachProtocolError):
        parser.feed(wire[length:])


@pytest.mark.parametrize("invalid", [None, "text", bytearray(b"x"), memoryview(b"x"), 123])
def test_bad_input_type_does_not_consume_partial_frame(invalid):
    parser = DockerAttachParser()
    wire = frame(1, b"good")
    parser.feed(wire[:3])
    with pytest.raises(TypeError):
        parser.feed(invalid)
    parser.feed(wire[3:])
    assert parser.finish().stdout.text == "good"


def test_oversized_chunk_rejected_without_advancing():
    parser = DockerAttachParser()
    wire = frame(1, b"good")
    parser.feed(wire[:9])
    with pytest.raises(ValueError):
        parser.feed(b"x" * (MAX_ATTACH_CHUNK_BYTES + 1))
    parser.feed(wire[9:])
    assert parser.finish().stdout.text == "good"


def test_payload_header_bytes_are_not_reinterpreted():
    parser = DockerAttachParser()
    body = frame(2, b"nested")
    parser.feed(frame(1, body))
    result = parser.finish()
    assert result.stdout.text == body.decode()
    assert result.stderr.text == ""


def test_empty_stream_idempotence_and_post_finish_rejection():
    parser = DockerAttachParser()
    parser.feed(b"")
    result = parser.finish()
    assert result.stdout.text == result.stderr.text == ""
    assert parser.finish() is result
    with pytest.raises(FrozenInstanceError):
        result.stdout = result.stderr
    for chunk in (b"", frame(1, b"late")):
        with pytest.raises(RuntimeError):
            parser.feed(chunk)


def test_capture_truncated_utf8_and_instance_isolation():
    first, second = DockerAttachParser(), DockerAttachParser()
    body = b"x" * 65535 + "你".encode()
    feed_blocks(first, frame(1, body))
    second.feed(frame(2, b"separate"))
    result = first.finish()
    assert result.stdout.text == "x" * 65535 + "\ufffd"
    assert result.stdout.capture_truncated
    assert second.finish().stderr.text == "separate"
