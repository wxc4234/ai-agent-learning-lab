"""显式清理专项，真实 Docker 测试默认关闭。"""

import asyncio
import json
import os
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime import docker_client as client
from app.services.runtime import sandbox_cleanup as service
from app.services.runtime.sandbox_creation import create_and_confirm_sandbox
from app.services.runtime.sandbox_identity import confirm_created_sandbox_identity
from app.services.runtime.sandbox_spec import build_sandbox_create_spec
from tests.runtime.test_sandbox_creation import CID, TOKEN, payload, request


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario(), 3))


def install(monkeypatch, *, data=None, fail=None, present=False):
    calls = []
    async def runner(args):
        calls.append(args)
        operation = args[1]
        if operation == fail:
            raise client.DockerClientError("docker_client_timeout")
        text = {"inspect": json.dumps(payload() if data is None else data),
                "rm": CID + "\n", "ls": CID + "\n" if present else ""}[operation]
        return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))
    monkeypatch.setattr(client, "_run_docker_client", runner)
    return calls


async def cleanup(**kwargs):
    return await service.cleanup_created_sandbox(**{
        "request": request(), "execution_token": TOKEN, "expected_container_id": CID,
    } | kwargs)


def test_success_fixed_operations_and_immutable_result(monkeypatch):
    async def scenario():
        calls = install(monkeypatch)
        result = await cleanup()
        assert result.container_id == CID and result.execution_token == TOKEN
        assert calls == [("container", "inspect", f"agent-sandbox-{TOKEN}"),
                         ("container", "rm", CID),
                         ("container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"id={CID}")]
        with pytest.raises(FrozenInstanceError):
            result.container_id = "b" * 64
    run(scenario)


@pytest.mark.parametrize("value", [None, True, 1, "", "a" * 12, "A" * 64, CID + "\n", "--force"])
def test_invalid_id_never_calls_docker(monkeypatch, value):
    async def scenario():
        calls = install(monkeypatch)
        with pytest.raises(ValueError):
            await cleanup(expected_container_id=value)
        assert calls == []
        for adapter in (client.remove_sandbox_container, client.is_sandbox_container_absent):
            with pytest.raises(ValueError):
                await adapter(container_id=value)
        assert calls == []
    run(scenario)


@pytest.mark.parametrize("kind", ["token", "request", "directory"])
def test_invalid_context_never_calls_docker(monkeypatch, kind):
    async def scenario():
        calls = install(monkeypatch)
        options = {"execution_token": "bad"} if kind == "token" else {"request": {}}
        if kind == "directory":
            command = request()
            command.working_directory = "src"
            options = {"request": command}
        with pytest.raises((TypeError, ValueError)):
            await cleanup(**options)
        assert calls == []
    run(scenario)


@pytest.mark.parametrize("field", ["id", "name", "label", "image", "running", "exited"])
def test_identity_or_state_mismatch_never_deletes(monkeypatch, field):
    async def scenario():
        data = payload()
        if field == "id":
            data[0]["Id"] = "b" * 64
        elif field == "name":
            data[0]["Name"] = "/other"
        elif field == "label":
            data[0]["Config"]["Labels"]["ai-agent-learning-lab.execution"] = "c" * 32
        elif field == "image":
            data[0]["Config"]["Image"] = "other"
        elif field == "running":
            data[0]["State"]["Running"] = True
        else:
            data[0]["State"]["Status"] = "exited"
        calls = install(monkeypatch, data=data)
        with pytest.raises(service.SandboxCleanupUnconfirmed):
            await cleanup()
        assert len(calls) == 1
    run(scenario)


@pytest.mark.parametrize("stage", ["inspect", "rm", "ls"])
def test_failures_no_retry_and_safe_error(monkeypatch, stage):
    async def scenario():
        calls = install(monkeypatch, fail=stage)
        with pytest.raises(service.SandboxCleanupUnconfirmed) as caught:
            await cleanup()
        assert str(caught.value) == "Sandbox 清理结果未确认"
        assert caught.value.container_id == CID and caught.value.execution_token == TOKEN
        assert caught.value.__suppress_context__
        assert [args[1] for args in calls] == ["inspect", "rm", "ls"][:["inspect", "rm", "ls"].index(stage) + 1]
    run(scenario)


def test_successful_remove_but_present_is_unconfirmed(monkeypatch):
    async def scenario():
        calls = install(monkeypatch, present=True)
        with pytest.raises(service.SandboxCleanupUnconfirmed):
            await cleanup()
        assert len(calls) == 3
    run(scenario)


@pytest.mark.parametrize("operation", ["remove", "absent"])
@pytest.mark.parametrize("text", ["", CID, CID + "\n", CID + "\r\n", "PRIVATE", "b" * 64, CID + "\nextra", "\n"])
def test_adapters_strict_response(monkeypatch, operation, text):
    async def scenario():
        async def runner(args):
            return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))
        monkeypatch.setattr(client, "_run_docker_client", runner)
        valid = text in (CID, CID + "\n", CID + "\r\n") or (operation == "absent" and text == "")
        adapter = client.remove_sandbox_container if operation == "remove" else client.is_sandbox_container_absent
        if valid:
            result = await adapter(container_id=CID)
            assert result is (None if operation == "remove" else text == "")
        else:
            with pytest.raises(client.DockerClientError, match="docker_response_unusable"):
                await adapter(container_id=CID)
    run(scenario)


@pytest.mark.parametrize("stage", ["inspect", "rm", "ls"])
def test_cancel_propagates_without_followup_operations(monkeypatch, stage):
    async def scenario():
        calls = install(monkeypatch)
        original = client._run_docker_client
        entered, finished = asyncio.Event(), asyncio.Event()
        async def runner(args):
            if args[1] != stage:
                return await original(args)
            calls.append(args)
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                finished.set()
        monkeypatch.setattr(client, "_run_docker_client", runner)
        task = asyncio.create_task(cleanup())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert calls[-1][1] == stage
    run(scenario)


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_CLEANUP_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("lost_response", [False, True])
def test_real_cleanup_and_lost_response(monkeypatch, lost_response):
    async def scenario():
        token, command = uuid4().hex, request()
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        original = service.remove_sandbox_container
        removals = []
        async def remove(*, container_id):
            removals.append(container_id)
            await original(container_id=container_id)
            if lost_response:
                raise client.DockerClientError("docker_client_timeout")
        monkeypatch.setattr(service, "remove_sandbox_container", remove)
        try:
            # 错ID拒绝后，原目标仍存在；不依赖名称删除。
            with pytest.raises(service.SandboxCleanupUnconfirmed):
                await service.cleanup_created_sandbox(request=command, execution_token=token,
                                                      expected_container_id="f" * 64)
            assert removals == []
            arguments = {"request": command, "execution_token": token,
                         "expected_container_id": identity.container_id}
            if lost_response:
                with pytest.raises(service.SandboxCleanupUnconfirmed):
                    await service.cleanup_created_sandbox(**arguments)
            else:
                result = await service.cleanup_created_sandbox(**arguments)
                assert result.container_id == identity.container_id
            assert removals == [identity.container_id]
            assert await client.is_sandbox_container_absent(container_id=identity.container_id)
            # 再次显式请求也不把inspect失败当作幂等成功，更不再次rm。
            with pytest.raises(service.SandboxCleanupUnconfirmed):
                await service.cleanup_created_sandbox(**arguments)
            assert len(removals) == 1
        finally:
            if not await client.is_sandbox_container_absent(container_id=identity.container_id):
                text = await client.inspect_sandbox_container(execution_token=token)
                confirm_created_sandbox_identity(container_id=identity.container_id, inspect_stdout=text,
                    spec=build_sandbox_create_spec(request=command, execution_token=token))
                await original(container_id=identity.container_id)
            assert await client.is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 60))
