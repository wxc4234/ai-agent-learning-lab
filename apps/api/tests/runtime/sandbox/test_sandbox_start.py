"""启动编排与取消收尾专项，真实Docker需显式启用。"""

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_start as service
from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_isolation_policy import fixture
from tests.runtime.sandbox.test_sandbox_stop import data


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario(), 3))


async def start(**kwargs):
    return await service.start_and_confirm_sandbox(**{
        "request": request(), "execution_token": TOKEN, "expected_container_id": CID,
    } | kwargs)


def install(monkeypatch, *, after="running", failure=None, invalid=False):
    calls = []
    async def runner(args):
        calls.append(args)
        number = len(calls)
        if failure == number:
            raise client.DockerClientError("docker_client_timeout")
        if args[1] == "start":
            text = CID + "\n"
        else:
            value = fixture()
            value[0]["State"] = data("created" if number == 1 else after)[0]["State"]
            if invalid:
                value[0]["HostConfig"]["Privileged"] = True
            text = json.dumps(value)
        return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))
    monkeypatch.setattr(client, "_run_docker_client", runner)
    stops = []
    async def settle(**kwargs):
        stops.append(kwargs)
        return SimpleNamespace(stopped=True)
    monkeypatch.setattr(service, "stop_and_confirm_sandbox", settle)
    return calls, stops


@pytest.mark.parametrize("after", ["running", "exited"])
def test_success_running_or_fast_exit(monkeypatch, after):
    async def scenario():
        calls, stops = install(monkeypatch, after=after)
        assert (await start()).status == after
        assert calls == [("container", "inspect", CID), ("container", "start", CID), ("container", "inspect", CID)]
        assert stops == []
    run(scenario)


@pytest.mark.parametrize("value", [None, True, "", "a" * 12, CID + "\n"])
def test_invalid_id_never_contacts_docker(monkeypatch, value):
    async def scenario():
        calls, stops = install(monkeypatch)
        with pytest.raises(ValueError):
            await start(expected_container_id=value)
        with pytest.raises(ValueError):
            await client.start_sandbox_container(container_id=value)
        assert calls == stops == []
    run(scenario)


@pytest.mark.parametrize("options", [{"request": {}}, {"execution_token": "bad"},
    {"request": CommandRequest(argv=["relative"])}])
def test_preflight_failure(monkeypatch, options):
    async def scenario():
        calls, stops = install(monkeypatch)
        with pytest.raises((TypeError, ValueError)):
            await start(**options)
        assert calls == stops == []
    run(scenario)


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_failure_settles_only_after_start_attempt(monkeypatch, failure):
    async def scenario():
        calls, stops = install(monkeypatch, failure=failure)
        with pytest.raises(service.SandboxStartUnconfirmed) as caught:
            await start()
        assert str(caught.value) == "Sandbox 启动结果未确认"
        assert caught.value.execution_token == TOKEN and caught.value.container_id == CID
        assert caught.value.stop_confirmed is (failure > 1)
        assert len(calls) == failure and len(stops) == (1 if failure > 1 else 0)
    run(scenario)


def test_invalid_policy_does_not_start_or_stop(monkeypatch):
    async def scenario():
        calls, stops = install(monkeypatch, invalid=True)
        with pytest.raises(service.SandboxStartUnconfirmed):
            await start()
        assert len(calls) == 1 and stops == []
    run(scenario)


@pytest.mark.parametrize("after", ["created", "paused", "dead"])
def test_unconfirmed_after_state_triggers_stop(monkeypatch, after):
    async def scenario():
        calls, stops = install(monkeypatch, after=after)
        with pytest.raises(service.SandboxStartUnconfirmed):
            await start()
        assert len(calls) == 3 and len(stops) == 1
    run(scenario)


def test_stop_failure_remains_unconfirmed(monkeypatch):
    async def scenario():
        install(monkeypatch, failure=2)
        async def broken(**kwargs):
            raise RuntimeError("PRIVATE")
        monkeypatch.setattr(service, "stop_and_confirm_sandbox", broken)
        with pytest.raises(service.SandboxStartUnconfirmed) as caught:
            await start()
        assert not caught.value.stop_confirmed
        assert "PRIVATE" not in str(caught.value)
    run(scenario)


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_cancel_then_repeated_cancel_waits_for_settlement(monkeypatch, stage):
    async def scenario():
        calls, stops = install(monkeypatch)
        original = client._run_docker_client
        entered, settling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def runner(args):
            if len(calls) + 1 != stage:
                return await original(args)
            calls.append(args)
            entered.set()
            await asyncio.Future()
        async def settle(**kwargs):
            stops.append(kwargs)
            settling.set()
            await release.wait()
            return SimpleNamespace(stopped=True)
        monkeypatch.setattr(client, "_run_docker_client", runner)
        monkeypatch.setattr(service, "stop_and_confirm_sandbox", settle)
        command = request()
        task = asyncio.create_task(start(request=command))
        try:
            await entered.wait()
            command.argv.clear()  # 收尾应使用调用开始时的副本。
            task.cancel()
            if stage > 1:
                await settling.wait()
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert stops[0]["request"].argv == request().argv
            release.set()
            with pytest.raises(service.SandboxStartCancelled) as caught:
                await task
            assert isinstance(caught.value, asyncio.CancelledError)
            assert caught.value.stop_confirmed is (stage > 1)
            assert caught.value.container_id == CID
            assert len(stops) == (1 if stage > 1 else 0)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "sandbox-start-settlement" and not t.done()]
    run(scenario)


@pytest.mark.parametrize("text", [CID, CID + "\n", CID + "\r\n", "", "PRIVATE", "b" * 64])
def test_start_receipt(monkeypatch, text):
    async def scenario():
        async def runner(args):
            assert args == ("container", "start", CID)
            return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))
        monkeypatch.setattr(client, "_run_docker_client", runner)
        if text.startswith(CID):
            await client.start_sandbox_container(container_id=CID)
        else:
            with pytest.raises(client.DockerClientError):
                await client.start_sandbox_container(container_id=CID)
    run(scenario)


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_START_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("mode", ["running", "fast_exit", "lost_response", "cancel"])
def test_real_start_and_failure_settlement(monkeypatch, mode):
    async def scenario():
        token = uuid4().hex
        program = "pass" if mode == "fast_exit" else "import time; time.sleep(60)"
        command = CommandRequest(argv=["/usr/local/bin/python", "-c", program])
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        real_start = client.start_sandbox_container
        calls = []
        started = asyncio.Event()
        async def wrapped(*, container_id):
            calls.append(container_id)
            await real_start(container_id=container_id)
            started.set()
            if mode == "lost_response":
                raise client.DockerClientError("docker_client_timeout")
            if mode == "cancel":
                await asyncio.Future()
            if mode == "fast_exit":
                # 测试屏障确保覆盖快速退出分支，产品代码不等待命令退出。
                async with asyncio.timeout(5):
                    while True:
                        text = await client.inspect_sandbox_container_by_id(container_id=container_id)
                        if json.loads(text)[0]["State"]["Status"] == "exited":
                            break
                        await asyncio.sleep(0.05)
        monkeypatch.setattr(service, "start_sandbox_container", wrapped)
        arguments = {"request": command, "execution_token": token, "expected_container_id": identity.container_id}
        task = asyncio.create_task(service.start_and_confirm_sandbox(**arguments))
        try:
            if mode == "cancel":
                await started.wait()
                task.cancel()
                with pytest.raises(service.SandboxStartCancelled) as caught:
                    await task
                assert caught.value.stop_confirmed
            elif mode == "lost_response":
                with pytest.raises(service.SandboxStartUnconfirmed) as caught:
                    await task
                assert caught.value.stop_confirmed
            else:
                result = await task
                assert result.status == ("exited" if mode == "fast_exit" else "running")
            assert calls == [identity.container_id]
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            state = await stop_and_confirm_sandbox(**arguments)
            assert state.stopped
            await client.remove_sandbox_container(container_id=identity.container_id)
            assert await client.is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 60))


def test_error_then_cancel_during_settlement_keeps_cancel_semantics(monkeypatch):
    async def scenario():
        install(monkeypatch, failure=2)
        entered, release = asyncio.Event(), asyncio.Event()
        async def settle(**kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(stopped=True)
        monkeypatch.setattr(service, "stop_and_confirm_sandbox", settle)
        task = asyncio.create_task(start())
        try:
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(service.SandboxStartCancelled) as caught:
                await task
            assert caught.value.stop_confirmed
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    run(scenario)


@pytest.mark.parametrize("stage", [1, 3])
def test_wrong_identity_before_or_after_start(monkeypatch, stage):
    async def scenario():
        calls, stops = install(monkeypatch)
        original = client._run_docker_client
        async def runner(args):
            result = await original(args)
            if len(calls) == stage:
                value = json.loads(result.streams.stdout.text)
                value[0]["Config"]["Labels"]["ai-agent-learning-lab.execution"] = "c" * 32
                result.streams.stdout.text = json.dumps(value)
            return result
        monkeypatch.setattr(client, "_run_docker_client", runner)
        with pytest.raises(service.SandboxStartUnconfirmed):
            await start()
        assert len(calls) == stage
        assert len(stops) == (1 if stage == 3 else 0)
    run(scenario)
