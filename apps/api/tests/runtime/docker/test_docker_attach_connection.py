"""attach 连接所有权、取消收尾及显式启用的本机传输验证。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime.docker import docker_attach_connection as service
from app.services.runtime.docker.docker_attach_http import build_docker_attach_request
from app.services.runtime.docker.docker_attach_stream import drain_docker_attach
from tests.runtime.docker.test_docker_attach_http import response
from tests.runtime.docker.test_docker_attach_parser import frame

CID = "a" * 64


def install(monkeypatch, *, stage=None, error=None, close_error=False, close_gate=None):
    calls = []
    raw = SimpleNamespace(setblocking=lambda value: calls.append(("blocking", value)),
                          close=lambda: calls.append("raw-close"))
    reader = asyncio.StreamReader()
    reader.feed_data(response() + frame(1, b"hello"))
    reader.feed_eof()
    entered = asyncio.Event()

    async def step(name):
        calls.append(name)
        if stage == name:
            entered.set()
            if error is not None:
                raise error
            await asyncio.Future()

    class Writer:
        transport = SimpleNamespace(abort=lambda: calls.append("abort"))

        def write(self, data):
            calls.append(("write", data))
            if stage == "write":
                raise error

        async def drain(self):
            await step("drain")

        def close(self):
            calls.append("close")

        async def wait_closed(self):
            calls.append("wait-closed")
            if close_gate is not None:
                entered.set()
                await close_gate.wait()
            if close_error:
                raise OSError("private close failure")

    async def connect(sock, path):
        assert sock is raw and path == str(service.DOCKER_ATTACH_SOCKET)
        await step("connect")

    async def open_stream(**kwargs):
        assert kwargs == {"sock": raw, "limit": 4096}
        await step("open")
        return reader, Writer()

    actual_handshake = service.read_docker_attach_upgrade

    async def handshake(stream):
        await step("handshake")
        await actual_handshake(stream)

    def make_socket(family, kind):
        assert family == service.socket.AF_UNIX and kind == service.socket.SOCK_STREAM
        calls.append("socket")
        return raw

    monkeypatch.setattr(service.socket, "socket", make_socket)
    monkeypatch.setattr(asyncio.get_running_loop(), "sock_connect", connect)
    monkeypatch.setattr(service.asyncio, "open_unix_connection", open_stream)
    monkeypatch.setattr(service, "read_docker_attach_upgrade", handshake)
    return calls, entered


def test_success_uses_fixed_target_and_preserves_frames(monkeypatch):
    async def scenario():
        calls, _ = install(monkeypatch)
        monkeypatch.setenv("DOCKER_HOST", "tcp://evil:2375")
        async with service.open_docker_attach(container_id=CID) as reader:
            assert (await drain_docker_attach(reader)).stdout.text == "hello"
            assert "close" not in calls
        assert ("write", build_docker_attach_request(container_id=CID)) in calls
        assert calls.count("socket") == calls.count("close") == calls.count("wait-closed") == 1
        assert "raw-close" not in calls and "abort" not in calls
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "docker-attach-close"]
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["connect", "open", "write", "drain", "handshake"])
def test_setup_failure_closes_owned_resource_once(monkeypatch, stage):
    async def scenario():
        calls, _ = install(monkeypatch, stage=stage, error=OSError("PRIVATE path"))
        with pytest.raises(service.DockerAttachConnectionError) as caught:
            async with service.open_docker_attach(container_id=CID):
                pytest.fail("failed handshake must not yield")
        assert "PRIVATE" not in str(caught.value)
        assert calls.count("socket") == 1
        assert calls.count("raw-close" if stage in ("connect", "open") else "close") == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["connect", "open", "drain", "handshake"])
@pytest.mark.parametrize("cancel", [False, True])
def test_setup_timeout_and_external_cancel(monkeypatch, stage, cancel):
    async def scenario():
        calls, entered = install(monkeypatch, stage=stage)
        monkeypatch.setattr(service, "ATTACH_CONNECT_TIMEOUT_SECONDS", 1 if cancel else 0.01)

        async def run():
            async with service.open_docker_attach(container_id=CID):
                pytest.fail("must not yield")

        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else service.DockerAttachConnectionError):
                await asyncio.wait_for(task, 1)
            assert calls.count("raw-close" if stage in ("connect", "open") else "close") == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("error", [RuntimeError("body failure"), asyncio.CancelledError()])
@pytest.mark.parametrize("close_error", [False, True])
def test_body_failure_is_preserved(monkeypatch, error, close_error):
    async def scenario():
        calls, _ = install(monkeypatch, close_error=close_error)
        with pytest.raises(type(error)) as caught:
            async with service.open_docker_attach(container_id=CID):
                raise error
        assert caught.value is error
        assert calls.count("close") == 1
        assert ("abort" in calls) is close_error
        if close_error:
            assert "关闭未确认" in caught.value.__notes__[-1]
    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [False, True])
def test_close_failure_is_not_silent_success(monkeypatch, timeout):
    async def scenario():
        calls, _ = install(monkeypatch, close_error=not timeout,
                           close_gate=asyncio.Event() if timeout else None)
        monkeypatch.setattr(service, "ATTACH_CLOSE_TIMEOUT_SECONDS", 0.01)
        with pytest.raises(service.DockerAttachCloseError):
            async with service.open_docker_attach(container_id=CID):
                pass
        assert calls.count("abort") == 1
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "docker-attach-close"]
    asyncio.run(scenario())


def test_repeated_cancellation_waits_for_close(monkeypatch):
    async def scenario():
        gate = asyncio.Event()
        calls, entered = install(monkeypatch, close_gate=gate)

        async def run():
            async with service.open_docker_attach(container_id=CID):
                pass

        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            gate.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert calls.count("wait-closed") == 1
        finally:
            gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_handshake_budget_does_not_limit_body(monkeypatch):
    async def scenario():
        install(monkeypatch)
        monkeypatch.setattr(service, "ATTACH_CONNECT_TIMEOUT_SECONDS", 0.02)
        async with service.open_docker_attach(container_id=CID):
            await asyncio.sleep(0.04)
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, "short", "A" * 64])
def test_invalid_id_before_socket_creation(monkeypatch, value):
    async def scenario():
        calls, _ = install(monkeypatch)
        with pytest.raises(ValueError):
            async with service.open_docker_attach(container_id=value):
                pytest.fail("invalid target")
        assert calls == []
    asyncio.run(scenario())


@pytest.mark.skipif(os.environ.get("RUN_ATTACH_SOCKET_TESTS") != "1", reason="explicit local socket opt-in")
def test_real_unix_socket_request_handshake_and_peer_eof(monkeypatch):
    import tempfile
    from pathlib import Path

    async def scenario():
        done = asyncio.get_running_loop().create_future()

        async def handle(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                assert request == build_docker_attach_request(container_id=CID)
                writer.write(response() + frame(1, b"real socket"))
                await writer.drain()
                assert await reader.read() == b""
                done.set_result(True)
            except Exception as error:  # noqa: BLE001 -- 把服务端断言传回测试任务。
                done.set_exception(error)
            finally:
                writer.close()
                await writer.wait_closed()

        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            path = Path(folder) / "attach.sock"
            monkeypatch.setattr(service, "DOCKER_ATTACH_SOCKET", path)
            server = await asyncio.start_unix_server(handle, path=str(path))
            async with server:
                async with service.open_docker_attach(container_id=CID) as reader:
                    assert await reader.readexactly(len(frame(1, b"real socket"))) == frame(1, b"real socket")
                assert await asyncio.wait_for(done, 1)
    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.skipif(os.environ.get("RUN_ATTACH_DOCKER_TESTS") != "1", reason="explicit Docker opt-in")
def test_real_created_container_handshake_and_cleanup():
    from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
    from app.services.runtime.sandbox.sandbox_cleanup import cleanup_created_sandbox
    from tests.runtime.sandbox.test_sandbox_creation import request

    async def scenario():
        command, token = request(), uuid4().hex
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        try:
            async with service.open_docker_attach(container_id=identity.container_id) as reader:
                assert isinstance(reader, asyncio.StreamReader)
        finally:
            # 本轮随机身份且未启动；既有服务重新核对created后清理并查询缺失。
            await cleanup_created_sandbox(request=command, execution_token=token,
                                          expected_container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_connect_and_send_share_one_budget(monkeypatch):
    async def scenario():
        calls, _ = install(monkeypatch)
        original_connect = asyncio.get_running_loop().sock_connect
        original_open = service.asyncio.open_unix_connection

        async def connect(*args):
            await asyncio.sleep(0.03)
            await original_connect(*args)

        async def open_stream(**kwargs):
            reader, writer = await original_open(**kwargs)
            original_drain = writer.drain

            async def drain():
                await asyncio.sleep(0.03)
                await original_drain()

            writer.drain = drain
            return reader, writer

        monkeypatch.setattr(asyncio.get_running_loop(), "sock_connect", connect)
        monkeypatch.setattr(service.asyncio, "open_unix_connection", open_stream)
        monkeypatch.setattr(service, "ATTACH_CONNECT_TIMEOUT_SECONDS", 0.05)
        with pytest.raises(service.DockerAttachConnectionError):
            async with service.open_docker_attach(container_id=CID):
                pytest.fail("per-phase timeout would incorrectly allow this handshake")
        assert "handshake" not in calls
        assert "close" in calls or "raw-close" in calls
    asyncio.run(scenario())


def test_setup_failure_retained_when_close_also_fails(monkeypatch):
    async def scenario():
        calls, _ = install(monkeypatch, stage="handshake", error=ValueError("PRIVATE"), close_error=True)
        with pytest.raises(service.DockerAttachConnectionError) as caught:
            async with service.open_docker_attach(container_id=CID):
                pytest.fail("must not yield")
        assert "关闭未确认" in caught.value.__notes__[-1]
        assert "PRIVATE" not in str(caught.value)
        assert calls.count("abort") == 1
    asyncio.run(scenario())
