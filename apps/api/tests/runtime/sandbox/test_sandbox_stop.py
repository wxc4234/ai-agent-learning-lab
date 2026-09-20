"""运行状态和停止编排专项，真实测试显式启用。"""

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_stop as service
from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
from app.services.runtime.sandbox.sandbox_identity import SandboxIdentityError
from app.services.runtime.sandbox.sandbox_isolation_policy import confirm_sandbox_isolation_policy
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, payload, request

SPEC = build_sandbox_create_spec(request=request(), execution_token=TOKEN)


def data(status="running"):
    result = payload()
    result[0]["State"] = {"Status": status, "Running": status == "running",
        "Pid": 123 if status == "running" else 0, "Paused": False, "Restarting": False, "Dead": False}
    return result


def parse(value):
    return service.confirm_sandbox_state(container_id=CID, inspect_stdout=json.dumps(value), spec=SPEC)


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario(), 3))


@pytest.mark.parametrize("status", ["created", "running", "exited"])
def test_consistent_states(status):
    result = parse(data(status))
    assert result.status == status
    assert result.stopped is (status != "running")
    assert result.identity.container_id == CID


@pytest.mark.parametrize("field,value", [("Status", s) for s in ("paused", "restarting", "dead", "removing", "unknown", None, [])] + [
    ("Pid", True), ("Pid", "123"), ("Pid", -1), ("Pid", 0), ("Pid", 1.0),
    ("Running", False), ("Running", 1), ("Running", None),
    ("Paused", True), ("Paused", 0), ("Restarting", True), ("Dead", True),
])
def test_inconsistent_or_unsupported_state(field, value):
    value_data = data()
    value_data[0]["State"][field] = value
    with pytest.raises(SandboxIdentityError):
        parse(value_data)


@pytest.mark.parametrize("field", ["Status", "Pid", "Running", "Paused", "Restarting", "Dead"])
def test_required_state_fields(field):
    value = data()
    del value[0]["State"][field]
    with pytest.raises(SandboxIdentityError):
        parse(value)


@pytest.mark.parametrize("status", ["created", "exited"])
@pytest.mark.parametrize("field,value", [("Running", True), ("Pid", 5)])
def test_stopped_must_have_no_process(status, field, value):
    value_data = data(status)
    value_data[0]["State"][field] = value
    with pytest.raises(SandboxIdentityError):
        parse(value_data)


def install(monkeypatch, before="running", after="exited", failure=None, mismatch=False):
    calls = []
    async def runner(args):
        calls.append(args)
        if failure == len(calls):
            raise client.DockerClientError("docker_client_timeout")
        if args[1] == "stop":
            text = CID + "\n"
        else:
            value = data(before if len(calls) == 1 else after)
            if mismatch:
                value[0]["Id"] = "c" * 64
            text = json.dumps(value)
        return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))
    monkeypatch.setattr(client, "_run_docker_client", runner)
    return calls


async def stop(**kwargs):
    return await service.stop_and_confirm_sandbox(**{
        "request": request(), "execution_token": TOKEN, "expected_container_id": CID,
    } | kwargs)


def test_stop_by_id_then_confirm(monkeypatch):
    async def scenario():
        calls = install(monkeypatch)
        assert (await stop()).stopped
        assert calls == [("container", "inspect", CID),
                         ("container", "stop", "--signal=SIGTERM", "--timeout=2", CID),
                         ("container", "inspect", CID)]
    run(scenario)


@pytest.mark.parametrize("status", ["created", "exited"])
def test_already_stopped_no_stop_request(monkeypatch, status):
    async def scenario():
        calls = install(monkeypatch, before=status)
        assert (await stop()).stopped
        assert len(calls) == 1
    run(scenario)


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_failure_safe_no_retry(monkeypatch, failure):
    async def scenario():
        calls = install(monkeypatch, failure=failure)
        with pytest.raises(service.SandboxStopUnconfirmed) as caught:
            await stop()
        assert str(caught.value) == "Sandbox 停止结果未确认"
        assert caught.value.execution_token == TOKEN and caught.value.container_id == CID
        assert len(calls) == failure
    run(scenario)


@pytest.mark.parametrize("mismatch", [False, True])
def test_wrong_identity_or_still_running_rejected(monkeypatch, mismatch):
    async def scenario():
        calls = install(monkeypatch, mismatch=mismatch, after="running")
        with pytest.raises(service.SandboxStopUnconfirmed):
            await stop()
        assert len(calls) == (1 if mismatch else 3)
    run(scenario)


@pytest.mark.parametrize("value", [None, "", "a" * 12, CID + "\n", "--all", True])
def test_bad_id_never_calls_docker(monkeypatch, value):
    async def scenario():
        calls = install(monkeypatch)
        with pytest.raises(ValueError):
            await stop(expected_container_id=value)
        for adapter in (client.stop_sandbox_container, client.inspect_sandbox_container_by_id):
            with pytest.raises(ValueError):
                await adapter(container_id=value)
        assert calls == []
    run(scenario)


@pytest.mark.parametrize("text", ["", "PRIVATE", "b" * 64, CID + "\nextra"])
def test_bad_stop_receipt(monkeypatch, text):
    async def scenario():
        async def runner(args):
            return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))
        monkeypatch.setattr(client, "_run_docker_client", runner)
        with pytest.raises(client.DockerClientError, match="docker_response_unusable"):
            await client.stop_sandbox_container(container_id=CID)
    run(scenario)


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_cancellation_propagates_without_followup(monkeypatch, stage):
    async def scenario():
        calls = install(monkeypatch)
        original = client._run_docker_client
        entered, finished = asyncio.Event(), asyncio.Event()
        async def runner(args):
            if len(calls) + 1 != stage:
                return await original(args)
            calls.append(args)
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                finished.set()
        monkeypatch.setattr(client, "_run_docker_client", runner)
        task = asyncio.create_task(stop())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and len(calls) == stage
    run(scenario)


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_STOP_DOCKER") != "1", reason="explicit Docker opt-in")
def test_real_stop_ignoring_sigterm_and_child():
    async def scenario():
        # readiness文件确保父子进程均已安装忽略SIGTERM，再请求停止。
        child = "import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path('/tmp/child-ready').touch(); time.sleep(60)"
        program = ("import signal,subprocess,time; from pathlib import Path; "
                   "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                   f"subprocess.Popen(['/usr/local/bin/python','-c',{child!r}],start_new_session=True); "
                   "Path('/tmp/parent-ready').touch(); time.sleep(60)")
        command = CommandRequest(argv=["/usr/local/bin/python", "-c", program])
        token = uuid4().hex
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        spec = build_sandbox_create_spec(request=command, execution_token=token)
        async def read_state():
            return service.confirm_sandbox_state(container_id=identity.container_id, spec=spec,
                inspect_stdout=await client.inspect_sandbox_container_by_id(container_id=identity.container_id))
        try:
            text = await client.inspect_sandbox_container_by_id(container_id=identity.container_id)
            confirm_sandbox_isolation_policy(request=command, execution_token=token,
                container_id=identity.container_id, inspect_stdout=text)
            # 仅测试准备直接start，不新增产品启动接口。
            await client._run_docker_client(("container", "start", identity.container_id))
            async with asyncio.timeout(5):
                while True:
                    result = await client._run_docker_client(("container", "exec", identity.container_id,
                        "/usr/local/bin/python", "-c", "from pathlib import Path; print(int(Path('/tmp/child-ready').exists() and Path('/tmp/parent-ready').exists()))"))
                    if result.streams.stdout.text == "1\n":
                        break
                    await asyncio.sleep(0.05)
            result = await service.stop_and_confirm_sandbox(request=command, execution_token=token,
                                                           expected_container_id=identity.container_id)
            assert result.status == "exited" and result.pid == 0 and result.stopped
            assert (await service.stop_and_confirm_sandbox(request=command, execution_token=token,
                    expected_container_id=identity.container_id)).stopped
            with pytest.raises(client.DockerClientError):
                await client._run_docker_client(("container", "exec", identity.container_id, "/bin/true"))
        finally:
            state = await read_state()
            if not state.stopped:
                await client.stop_sandbox_container(container_id=identity.container_id)
                state = await read_state()
            assert state.stopped
            await client.remove_sandbox_container(container_id=identity.container_id)
            assert await client.is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 60))
