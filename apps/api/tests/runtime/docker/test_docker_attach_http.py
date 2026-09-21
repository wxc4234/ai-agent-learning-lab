"""HTTP 升级响应边界；仅使用内存流及受控读取器。"""

import asyncio

import pytest

from app.services.runtime.docker import docker_attach_http as service
from app.services.runtime.docker.docker_attach_stream import drain_docker_attach
from tests.runtime.docker.test_docker_attach_parser import frame


FIELDS = [
    b"Connection: Upgrade",
    b"Upgrade: tcp",
    b"Content-Type: application/vnd.docker.multiplexed-stream",
]


def response(status=b"HTTP/1.1 101 UPGRADED", fields=None):
    return b"\r\n".join([status, *(FIELDS if fields is None else fields), b"", b""])


class CountingReader(asyncio.StreamReader):
    """观测消费量，证明失败和成功都不会越过头部读取边界。"""

    def __init__(self, wire):
        super().__init__()
        self.sizes = []
        self.feed_data(wire)
        self.feed_eof()

    async def read(self, n=-1):
        self.sizes.append(n)
        return await super().read(n)


@pytest.mark.parametrize("status", [b"HTTP/1.1 101 UPGRADED", b"HTTP/1.1 101 Switching Protocols", b"HTTP/1.1 101"])
@pytest.mark.parametrize("fields", [FIELDS, [
    b"uPgRaDe:\tTCP ", b"connection: keep-alive, UpGrade",
    b"CONTENT-TYPE: APPLICATION/VND.DOCKER.MULTIPLEXED-STREAM",
    b"X-Extra: harmless\tvalue",
]])
def test_valid_response_preserves_coalesced_first_frame(status, fields):
    async def scenario():
        header = response(status, fields)
        reader = CountingReader(header + frame(1, b"hello") + frame(2, b"warning"))
        assert await service.read_docker_attach_upgrade(reader) is None
        assert reader.sizes == [1] * len(header)
        result = await drain_docker_attach(reader)
        assert result.stdout.text == "hello" and result.stderr.text == "warning"
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [
    b"HTTP/1.1 200 OK", b"HTTP/1.1 404 missing", b"HTTP/1.1 500 error",
    b"HTTP/1.0 101 UPGRADED", b"HTTP/2 101", b"HTTP/1.1 1010",
    b"HTTP/1.1  101", b"HTTP/1.1 101\tUPGRADED", b"HTTP/1.1 101 \x00",
])
def test_rejects_status_without_consuming_error_body(status):
    async def scenario():
        header = response(status)
        reader = CountingReader(header + b"PRIVATE BODY")
        with pytest.raises(service.DockerAttachHandshakeError) as caught:
            await service.read_docker_attach_upgrade(reader)
        assert str(caught.value) == "无法确认 Docker attach HTTP 握手成功"
        assert reader.sizes == [1] * len(header)
        assert await reader.read() == b"PRIVATE BODY"
    asyncio.run(scenario())


@pytest.mark.parametrize("fields", [
    FIELDS[:index] + FIELDS[index + 1:] for index in range(3)
] + [
    [b"Connection: " + value, *FIELDS[1:]]
    for value in (b"", b"keep-alive", b"upgrade,close", b"upgrade,", b",upgrade", b"upgrade,,x", b"up grade", b"upgrade;foo")
] + [
    [FIELDS[0], b"Upgrade: " + value, FIELDS[2]]
    for value in (b"", b"websocket", b"tcp,other")
] + [
    [*FIELDS[:2], b"Content-Type: " + value]
    for value in (b"", b"application/json", b"application/vnd.docker.raw-stream", b"application/vnd.docker.multiplexed-stream; charset=utf-8")
] + [
    [*FIELDS, field]
    for field in (
        b"Content-Length: 0", b"Transfer-Encoding: chunked", b"Content-Encoding: identity",
        b"connection: Upgrade", b"UPGRADE: tcp", b"content-type: application/vnd.docker.multiplexed-stream",
        b"Bad Header: value", b"Upgrade : tcp", b": empty", b"no-colon",
        b" folded: value", b"X-Test: line\nnext", b"X-Test: line\rnext",
        b"X-Test: \x00", b"X-Test: \x7f", b"X-Test: \xff",
    )
] + [[*FIELDS, b"X-Test: a", b"x-test: b"]])
def test_rejects_ambiguous_or_unsupported_headers(fields):
    async def scenario():
        reader = CountingReader(response(fields=fields))
        with pytest.raises(service.DockerAttachHandshakeError):
            await service.read_docker_attach_upgrade(reader)
    asyncio.run(scenario())


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_header_size_includes_terminator(delta):
    async def scenario():
        empty = response(fields=[*FIELDS, b"X-Pad: "])
        size = service.MAX_ATTACH_HTTP_HEADER_BYTES + delta
        header = response(fields=[*FIELDS, b"X-Pad: " + b"x" * (size - len(empty))])
        assert len(header) == size
        reader = CountingReader(header + b"TAIL")
        if delta > 0:
            with pytest.raises(service.DockerAttachHandshakeError):
                await service.read_docker_attach_upgrade(reader)
            assert len(reader.sizes) == service.MAX_ATTACH_HTTP_HEADER_BYTES
            assert await reader.read() == header[service.MAX_ATTACH_HTTP_HEADER_BYTES:] + b"TAIL"
        else:
            await service.read_docker_attach_upgrade(reader)
            assert len(reader.sizes) == size
            assert await reader.read() == b"TAIL"
    asyncio.run(scenario())


@pytest.mark.parametrize("wire", [b"", b"HTTP/1.1", response()[:-1], response().replace(b"\r\n", b"\n")])
def test_eof_before_complete_header_fails(wire):
    async def scenario():
        reader = CountingReader(wire)
        with pytest.raises(service.DockerAttachHandshakeError):
            await service.read_docker_attach_upgrade(reader)
        assert len(reader.sizes) == len(wire) + 1
    asyncio.run(scenario())


@pytest.mark.parametrize("value,error", [(None, TypeError), (bytearray(), TypeError), ("", TypeError), (b"ab", ValueError)])
def test_invalid_reader_result(value, error):
    class Reader:
        async def read(self, n):
            assert n == 1
            return value

    with pytest.raises(error):
        asyncio.run(service.read_docker_attach_upgrade(Reader()))


@pytest.mark.parametrize("error", [OSError("private read failure"), RuntimeError("adapter"), asyncio.CancelledError()])
def test_read_errors_propagate_unchanged(error):
    class Reader:
        async def read(self, n):
            raise error

    with pytest.raises(type(error)) as caught:
        asyncio.run(service.read_docker_attach_upgrade(Reader()))
    assert caught.value is error


def test_waiting_timeout_leaves_stream_owned_by_caller(monkeypatch):
    monkeypatch.setattr(service, "ATTACH_HTTP_HEADER_TIMEOUT_SECONDS", 0.01)

    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/")
        with pytest.raises(service.DockerAttachHandshakeError):
            await asyncio.wait_for(service.read_docker_attach_upgrade(reader), 1)
        reader.feed_data(b"still open")
        reader.feed_eof()
        assert await reader.read() == b"still open"
    asyncio.run(scenario())


def test_trickle_does_not_reset_total_timeout(monkeypatch):
    monkeypatch.setattr(service, "ATTACH_HTTP_HEADER_TIMEOUT_SECONDS", 0.02)

    class Reader:
        calls = 0

        async def read(self, n):
            self.calls += 1
            await asyncio.sleep(0.002)
            return b"x"

    async def scenario():
        reader = Reader()
        with pytest.raises(service.DockerAttachHandshakeError):
            await asyncio.wait_for(service.read_docker_attach_upgrade(reader), 1)
        assert reader.calls < 100
    asyncio.run(scenario())


def test_immediate_reads_still_observe_timeout(monkeypatch):
    monkeypatch.setattr(service, "ATTACH_HTTP_HEADER_TIMEOUT_SECONDS", 0)

    async def scenario():
        reader = CountingReader(response())
        with pytest.raises(service.DockerAttachHandshakeError):
            await service.read_docker_attach_upgrade(reader)
        assert len(reader.sizes) < len(response())
    asyncio.run(scenario())


@pytest.mark.parametrize("immediate", [False, True])
def test_external_cancellation_is_not_converted_to_handshake_error(immediate):
    async def scenario():
        entered = asyncio.Event()

        class Reader:
            calls = 0
            cancelled = False

            async def read(self, n):
                self.calls += 1
                entered.set()
                if immediate:
                    return b"x" if self.calls < 100 else b""
                try:
                    await asyncio.Future()
                finally:
                    self.cancelled = True

        reader = Reader()
        task = asyncio.create_task(service.read_docker_attach_upgrade(reader))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert reader.calls < 100
            assert immediate or reader.cancelled
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_fragmented_header_waits_and_concurrent_readers_are_independent():
    async def scenario():
        first = asyncio.StreamReader()
        second = CountingReader(response())
        task = asyncio.create_task(service.read_docker_attach_upgrade(first))
        try:
            first.feed_data(response()[:-1])
            await service.read_docker_attach_upgrade(second)
            assert not task.done()
            first.feed_data(response()[-1:] + frame(1, b"retained"))
            first.feed_eof()
            await asyncio.wait_for(task, 1)
            assert (await drain_docker_attach(first)).stdout.text == "retained"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("container_id", ["a" * 64, "0" * 64, "f" * 64, "0123456789abcdef" * 4])
def test_request_has_exact_fixed_wire_contract(container_id):
    result = service.build_docker_attach_request(container_id=container_id)
    # 独立写出预期协议，不复用实现常量，以捕获版本或能力策略意外变化。
    expected = (
        f"POST /v1.45/containers/{container_id}/attach"
        "?stream=1&logs=0&stdin=0&stdout=1&stderr=1 HTTP/1.1\r\n"
        "Host: docker\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: tcp\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    ).encode("ascii")
    assert type(result) is bytes
    assert result == expected
    assert result.count(b"\r\n\r\n") == 1
    assert result.split(b"\r\n\r\n", 1)[1] == b""


@pytest.mark.parametrize("container_id", [
    None, True, 123, b"a" * 64, bytearray(b"a" * 64), [], {},
    "", "a" * 12, "a" * 63, "a" * 65, "A" * 64, "g" * 64,
    "sandbox-name", " " + "a" * 64, "a" * 64 + " ",
    "a" * 64 + "\n", "a" * 64 + "\r\nUpgrade: evil",
    "a" * 64 + "?stdin=1", "../" + "a" * 64,
    "a" * 63 + "/", "a" * 63 + "\x00", "ａ" * 64,
])
def test_request_rejects_invalid_id_without_echoing_input(container_id):
    with pytest.raises(ValueError) as caught:
        service.build_docker_attach_request(container_id=container_id)
    assert str(caught.value) == "container_id 必须是完整小写容器 ID"


@pytest.mark.parametrize("extra", [
    {"stdin": True}, {"logs": True}, {"host": "evil"},
    {"api_version": "1.99"}, {"headers": {"X-Test": "evil"}},
])
def test_request_does_not_accept_caller_policy_overrides(extra):
    with pytest.raises(TypeError):
        service.build_docker_attach_request(container_id="a" * 64, **extra)


def test_request_is_keyword_only():
    with pytest.raises(TypeError):
        service.build_docker_attach_request("a" * 64)


def test_request_has_no_io_or_environment_target_override(monkeypatch):
    import builtins
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("request construction must not open files or sockets")

    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid:2375")
    monkeypatch.setenv("DOCKER_API_VERSION", "1.99")
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(socket, "socket", forbidden)
        first = service.build_docker_attach_request(container_id="a" * 64)
        second = service.build_docker_attach_request(container_id="b" * 64)
        repeated = service.build_docker_attach_request(container_id="a" * 64)
    assert first == repeated
    assert b"/v1.45/containers/" + b"b" * 64 + b"/attach?" in second
    assert b"Host: docker\r\n" in first
    assert b"untrusted" not in first and b"1.99" not in first
