"""订阅先于启动、并发排空及失败停止收尾；Docker测试显式启用。"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime.sandbox import sandbox_execution as service
from app.services.runtime.command.command_contracts import CommandRequest
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_isolation_policy import fixture
from tests.runtime.sandbox.test_sandbox_stop import data
from tests.runtime.docker.test_docker_attach_parser import frame


def install(monkeypatch, *, failure=None, invalid=None, code=0, hold=False, eof_before_start=False,
            stop_ok=True, close_error=False, oom=False, daemon_error=False):
    calls = []
    started = asyncio.Event()
    reading = asyncio.Event()
    stop_entered = asyncio.Event()
    stop_gate = asyncio.Event()
    stop_gate.set()
    values = {"status": "created", "inspect_count": 0}

    class Reader(asyncio.StreamReader):
        async def read(self, n=-1):
            reading.set()
            return await super().read(n)

    reader = Reader()

    async def inspect(*, container_id):
        assert container_id == CID
        calls.append("inspect")
        values["inspect_count"] += 1
        if failure == f"inspect-{values['inspect_count']}":
            raise OSError("PRIVATE inspect")
        value = fixture()
        value[0]["State"] = data(values["status"])[0]["State"]
        value[0]["State"].update(ExitCode=code, OOMKilled=oom, Error="PRIVATE daemon" if daemon_error else "")
        if invalid == values["inspect_count"]:
            value[0]["HostConfig"]["Privileged"] = True
        return json.dumps(value)

    @asynccontextmanager
    async def attach(*, container_id):
        calls.append("attach")
        if failure == "attach":
            raise OSError("PRIVATE attach")
        if eof_before_start:
            reader.feed_eof()
        try:
            yield reader
        finally:
            calls.append("close")
            if close_error:
                raise OSError("PRIVATE close")

    async def start(*, container_id):
        assert reading.is_set(), "must start drain before start request"
        calls.append("start")
        values["status"] = "running"
        started.set()
        if failure == "start":
            raise OSError("PRIVATE response lost")
        if failure == "protocol":
            reader.feed_data(frame(3, b"bad"))
        elif failure == "read":
            reader.set_exception(OSError("PRIVATE read"))
        else:
            reader.feed_data(frame(1, b"hello") + frame(2, b"warning"))
            if not hold:
                values["status"] = "exited"
                reader.feed_eof()

    async def stop(**kwargs):
        calls.append("stop")
        assert kwargs["execution_token"] == TOKEN and kwargs["expected_container_id"] == CID
        stop_entered.set()
        await stop_gate.wait()
        if not stop_ok:
            raise OSError("PRIVATE stop")
        values["status"] = "exited"
        return SimpleNamespace(stopped=True)

    monkeypatch.setattr(service, "inspect_sandbox_container_by_id", inspect)
    monkeypatch.setattr(service, "open_docker_attach", attach)
    monkeypatch.setattr(service, "start_sandbox_container", start)
    monkeypatch.setattr(service, "stop_and_confirm_sandbox", stop)
    return SimpleNamespace(calls=calls, reader=reader, started=started, values=values,
                           stop_entered=stop_entered, stop_gate=stop_gate)


async def execute(**kwargs):
    return await service.execute_created_sandbox(**{
        "request": request(), "execution_token": TOKEN, "expected_container_id": CID,
    } | kwargs)


def assert_no_owned_tasks():
    assert not [t for t in asyncio.all_tasks() if t.get_name() in ("sandbox-attach-output", "sandbox-execution-stop")]


@pytest.mark.parametrize("code,oom,daemon_error,success", [
    (0, False, False, True), (7, False, False, False), (137, False, False, False),
    (0, True, False, False), (0, False, True, False),
])
def test_success_order_and_exit_facts(monkeypatch, code, oom, daemon_error, success):
    async def scenario():
        lab = install(monkeypatch, code=code, oom=oom, daemon_error=daemon_error)
        result = await execute()
        assert lab.calls == ["inspect", "attach", "inspect", "start", "inspect", "close"]
        assert result.streams.stdout.text == "hello" and result.streams.stderr.text == "warning"
        assert result.exit.exit_code == code and result.succeeded is success
        assert result.exit.oom_killed is oom and result.exit.daemon_error is daemon_error
        assert result.duration_ms >= 0
        with pytest.raises(FrozenInstanceError):
            result.duration_ms = 1
        assert_no_owned_tasks()
    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [1, 2])
def test_policy_failure_never_starts_or_stops(monkeypatch, invalid):
    async def scenario():
        lab = install(monkeypatch, invalid=invalid)
        with pytest.raises(service.SandboxExecutionUnconfirmed) as caught:
            await execute()
        assert not caught.value.start_attempted and not caught.value.stop_confirmed
        assert "start" not in lab.calls and "stop" not in lab.calls
        assert ("attach" in lab.calls) is (invalid == 2)
        assert_no_owned_tasks()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["inspect-1", "attach", "inspect-2", "start", "inspect-3", "protocol", "read"])
@pytest.mark.parametrize("stop_ok", [False, True])
def test_failures_preserve_identity_and_stop_evidence(monkeypatch, failure, stop_ok):
    async def scenario():
        lab = install(monkeypatch, failure=failure, stop_ok=stop_ok)
        with pytest.raises(service.SandboxExecutionUnconfirmed) as caught:
            await execute()
        error = caught.value
        attempted = "start" in lab.calls
        assert error.execution_token == TOKEN and error.container_id == CID
        assert error.start_attempted is attempted
        assert error.stop_confirmed is (attempted and stop_ok)
        assert lab.calls.count("stop") == int(attempted)
        assert lab.calls.count("start") <= 1
        assert error.reason == "execution_failed" and "PRIVATE" not in str(error)
        assert_no_owned_tasks()
    asyncio.run(scenario())


def test_attach_eof_before_start_refuses_execution(monkeypatch):
    async def scenario():
        lab = install(monkeypatch, eof_before_start=True)
        with pytest.raises(service.SandboxExecutionUnconfirmed) as caught:
            await execute()
        assert not caught.value.start_attempted and "start" not in lab.calls
        assert "close" in lab.calls
    asyncio.run(scenario())


def test_exit_alone_is_not_complete_until_output_eof(monkeypatch):
    async def scenario():
        lab = install(monkeypatch, hold=True)
        task = asyncio.create_task(execute())
        try:
            await lab.started.wait()
            lab.values["status"] = "exited"
            await asyncio.sleep(0.06)
            assert not task.done()
            lab.reader.feed_data(frame(2, b"tail"))
            lab.reader.feed_eof()
            assert (await task).streams.stderr.text == "warningtail"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("stop_ok", [False, True])
def test_timeout_or_cancel_stops_and_joins(monkeypatch, cancel, stop_ok):
    async def scenario():
        lab = install(monkeypatch, hold=True, stop_ok=stop_ok)
        monkeypatch.setattr(service, "COMMAND_TIMEOUT_SECONDS", 1 if cancel else 0.02)
        task = asyncio.create_task(execute())
        try:
            await lab.started.wait()
            if cancel:
                task.cancel()
            with pytest.raises(service.SandboxExecutionCancelled if cancel else service.SandboxExecutionUnconfirmed) as caught:
                await task
            assert caught.value.start_attempted and caught.value.stop_confirmed is stop_ok
            if not cancel:
                assert caught.value.reason == "timed_out"
            assert lab.calls[-2:] == ["close", "stop"]
            assert_no_owned_tasks()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_repeated_cancel_waits_for_stop_and_frozen_request(monkeypatch):
    async def scenario():
        lab = install(monkeypatch, hold=True)
        lab.stop_gate.clear()
        original = request()
        actual_stop = service.stop_and_confirm_sandbox

        async def stop(**kwargs):
            assert kwargs["request"].argv == request().argv
            return await actual_stop(**kwargs)

        monkeypatch.setattr(service, "stop_and_confirm_sandbox", stop)
        task = asyncio.create_task(execute(request=original))
        try:
            await lab.started.wait()
            original.argv[:] = ["/changed"]
            task.cancel()
            await lab.stop_entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            lab.stop_gate.set()
            with pytest.raises(service.SandboxExecutionCancelled) as caught:
                await task
            assert caught.value.stop_confirmed
            assert_no_owned_tasks()
        finally:
            lab.stop_gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_close_failure_cannot_return_success(monkeypatch):
    async def scenario():
        lab = install(monkeypatch, close_error=True)
        with pytest.raises(service.SandboxExecutionUnconfirmed) as caught:
            await execute()
        assert caught.value.stop_confirmed
        assert lab.calls[-1] == "stop"
    asyncio.run(scenario())


@pytest.mark.parametrize("kwargs", [
    {"expected_container_id": "short"}, {"execution_token": "bad"},
    {"request": {}}, {"request": CommandRequest(argv=["relative"])},
])
def test_invalid_input_has_no_external_effect(monkeypatch, kwargs):
    async def scenario():
        lab = install(monkeypatch)
        with pytest.raises((TypeError, ValueError)):
            await execute(**kwargs)
        assert lab.calls == []
    asyncio.run(scenario())


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_EXECUTION_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("mode", ["fast", "nonzero", "large", "timeout", "cancel"])
def test_real_docker_execution_and_stopped_cleanup(monkeypatch, mode):
    from app.services.runtime.docker import docker_client as client
    from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
    from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
    from app.services.runtime.sandbox.sandbox_stop import confirm_sandbox_state, stop_and_confirm_sandbox

    async def scenario():
        program = "import os; os.write(1,b'first\\n'); os.write(2,b'last\\n')"
        if mode == "nonzero":
            program += "; raise SystemExit(7)"
        elif mode == "large":
            program = "import os; os.write(1,b'x'*200000); os.write(2,b'y'*200000)"
        elif mode in ("timeout", "cancel"):
            program = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
        command = CommandRequest(argv=["/usr/local/bin/python", "-c", program])
        token = uuid4().hex
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        task = None
        try:
            if mode == "timeout":
                monkeypatch.setattr(service, "COMMAND_TIMEOUT_SECONDS", 1)
            if mode == "cancel":
                # 用真实读到的ready输出作为取消屏障，避免靠sleep猜测启动完成。
                ready = asyncio.Event()
                actual_drain = service.drain_docker_attach

                async def drain(reader):
                    class ObservedReader:
                        async def read(self, n):
                            chunk = await reader.read(n)
                            if b"ready" in chunk:
                                ready.set()
                            return chunk
                    return await actual_drain(ObservedReader())

                monkeypatch.setattr(service, "drain_docker_attach", drain)
            task = asyncio.create_task(service.execute_created_sandbox(
                request=command, execution_token=token, expected_container_id=identity.container_id,
            ))
            if mode == "cancel":
                await asyncio.wait_for(ready.wait(), 5)
                task.cancel()
            if mode in ("timeout", "cancel"):
                with pytest.raises(service.SandboxExecutionCancelled if mode == "cancel" else service.SandboxExecutionUnconfirmed) as caught:
                    await task
                assert caught.value.start_attempted and caught.value.stop_confirmed
                if mode == "timeout":
                    assert caught.value.reason == "timed_out"
            else:
                result = await task
                assert result.exit.exit_code == (7 if mode == "nonzero" else 0)
                if mode == "large":
                    assert result.streams.stdout.text == "x" * 65536 and result.streams.stdout.truncated
                    assert result.streams.stderr.text == "y" * 65536 and result.streams.stderr.truncated
                else:
                    assert result.streams.stdout.text == "first\n" and result.streams.stderr.text == "last\n"
                assert result.succeeded is (mode != "nonzero")
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await stop_and_confirm_sandbox(request=command, execution_token=token,
                                           expected_container_id=identity.container_id)
            text = await client.inspect_sandbox_container_by_id(container_id=identity.container_id)
            spec = build_sandbox_create_spec(request=command, execution_token=token)
            state = confirm_sandbox_state(container_id=identity.container_id, inspect_stdout=text, spec=spec)
            assert state.stopped
            # 仅测试收尾：身份与停止状态重新确认后非强制删除本轮容器。
            # 不放宽生产created-only清理服务的契约。
            await client.remove_sandbox_container(container_id=identity.container_id)
            assert await client.is_sandbox_container_absent(container_id=identity.container_id)
        assert_no_owned_tasks()
    asyncio.run(asyncio.wait_for(scenario(), 45))


@pytest.mark.parametrize("stage", ["preflight", "attach", "second_check"])
@pytest.mark.parametrize("cancel", [False, True])
def test_prestart_cancel_or_timeout_does_not_start_or_stop(monkeypatch, stage, cancel):
    async def scenario():
        lab = install(monkeypatch)
        entered = asyncio.Event()
        actual_check = service._confirm_created
        checks = 0

        async def check(**kwargs):
            nonlocal checks
            checks += 1
            if checks == (1 if stage == "preflight" else 2) and stage != "attach":
                entered.set()
                await asyncio.Future()
            await actual_check(**kwargs)

        @asynccontextmanager
        async def attach(**kwargs):
            entered.set()
            await asyncio.Future()
            yield lab.reader

        monkeypatch.setattr(service, "_confirm_created", check)
        if stage == "attach":
            monkeypatch.setattr(service, "open_docker_attach", attach)
        monkeypatch.setattr(service, "COMMAND_TIMEOUT_SECONDS", 1 if cancel else 0.01)
        task = asyncio.create_task(execute())
        try:
            await entered.wait()
            if cancel:
                task.cancel()
            with pytest.raises(service.SandboxExecutionCancelled if cancel else service.SandboxExecutionUnconfirmed) as caught:
                await task
            assert not caught.value.start_attempted and not caught.value.stop_confirmed
            assert "start" not in lab.calls and "stop" not in lab.calls
            if not cancel:
                assert caught.value.reason == "timed_out"
            assert_no_owned_tasks()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["identity", "created", "paused", "exit_type"])
def test_bad_final_snapshot_cannot_become_success(monkeypatch, mutation):
    async def scenario():
        lab = install(monkeypatch)
        actual = service.inspect_sandbox_container_by_id

        async def inspect(**kwargs):
            text = await actual(**kwargs)
            if "start" not in lab.calls:
                return text
            value = json.loads(text)
            if mutation == "identity":
                value[0]["Id"] = "b" * 64
            elif mutation == "created":
                value[0]["State"] = data("created")[0]["State"]
            elif mutation == "paused":
                value[0]["State"]["Paused"] = True
            else:
                value[0]["State"]["ExitCode"] = True
            return json.dumps(value)

        monkeypatch.setattr(service, "inspect_sandbox_container_by_id", inspect)
        with pytest.raises(service.SandboxExecutionUnconfirmed):
            await execute()
        assert lab.calls.count("stop") == 1
        assert_no_owned_tasks()
    asyncio.run(scenario())


def test_cancel_after_failure_during_stop_remains_cancellation(monkeypatch):
    async def scenario():
        lab = install(monkeypatch, failure="start")
        lab.stop_gate.clear()
        task = asyncio.create_task(execute())
        try:
            await lab.stop_entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            lab.stop_gate.set()
            with pytest.raises(service.SandboxExecutionCancelled) as caught:
                await task
            assert caught.value.stop_confirmed and caught.value.start_attempted
            assert_no_owned_tasks()
        finally:
            lab.stop_gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
