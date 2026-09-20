"""创建编排专项；真实Docker测试需显式启用，只清理本轮确认的容器。"""

import asyncio
import json
import os
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime import docker_client as client
from app.services.runtime import sandbox_creation as service
from app.services.runtime.command_contracts import CommandRequest
from app.services.runtime.sandbox_identity import confirm_created_sandbox_identity
from app.services.runtime.sandbox_spec import APPROVED_SANDBOX_IMAGE, build_sandbox_create_spec

TOKEN = "b" * 32
CID = "a" * 64


def request():
    return CommandRequest(argv=["/usr/local/bin/python", "-c", "print('hello')"])


def payload(token=TOKEN, container_id=CID):
    return [{"Id": container_id, "Name": f"/agent-sandbox-{token}",
             "Config": {"Image": APPROVED_SANDBOX_IMAGE, "Labels": {
                 "ai-agent-learning-lab.role": "sandbox",
                 "ai-agent-learning-lab.execution": token}},
             "State": {"Status": "created", "Running": False, "Pid": 0}}]


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario(), 3))


def install(monkeypatch, *, create_output=CID + "\n", inspect_output=None,
            create_error=None, inspect_error=None):
    calls = []
    async def create(*, spec):
        calls.append(("create", spec))
        if create_error is not None:
            raise create_error
        return create_output
    async def inspect(*, execution_token):
        calls.append(("inspect", execution_token))
        if inspect_error is not None:
            raise inspect_error
        return json.dumps(payload()) if inspect_output is None else inspect_output
    monkeypatch.setattr(service, "create_sandbox_container", create)
    monkeypatch.setattr(service, "inspect_sandbox_container", inspect)
    return calls


def test_success_order_real_builder_parser_and_frozen_identity(monkeypatch):
    async def scenario():
        calls = install(monkeypatch)
        command = request()
        identity = await service.create_and_confirm_sandbox(request=command, execution_token=TOKEN)
        assert calls == [("create", build_sandbox_create_spec(request=command, execution_token=TOKEN)),
                         ("inspect", TOKEN)]
        assert identity.container_id == CID
        assert identity.container_name == f"agent-sandbox-{TOKEN}"
        assert identity.execution_token == TOKEN
        assert identity.image == APPROVED_SANDBOX_IMAGE
        with pytest.raises(FrozenInstanceError):
            identity.container_id = "c" * 64
    run(scenario)


@pytest.mark.parametrize("kind", ["token", "request_type", "relative_program", "working_directory", "mutated"])
def test_preflight_failure_never_calls_docker(monkeypatch, kind):
    async def scenario():
        calls = install(monkeypatch)
        command = request()
        token = TOKEN
        if kind == "token":
            token = "invalid"
        elif kind == "request_type":
            command = {}
        elif kind == "relative_program":
            command.argv = ["python"]
        elif kind == "working_directory":
            command.working_directory = "src"
        else:
            command.argv.clear()
        with pytest.raises((ValueError, TypeError)):
            await service.create_and_confirm_sandbox(request=command, execution_token=token)
        assert calls == []
    run(scenario)


@pytest.mark.parametrize("stage", ["create", "inspect"])
@pytest.mark.parametrize("error", [client.DockerClientError("docker_client_timeout"),
                                  client.DockerClientError("docker_response_unusable"),
                                  RuntimeError("PRIVATE"), OSError("PRIVATE")])
def test_external_failures_are_unconfirmed_without_retry(monkeypatch, stage, error):
    async def scenario():
        calls = install(monkeypatch, **{f"{stage}_error": error})
        with pytest.raises(service.SandboxCreationUnconfirmed) as caught:
            await service.create_and_confirm_sandbox(request=request(), execution_token=TOKEN)
        assert str(caught.value) == "Sandbox 创建结果未确认"
        assert caught.value.execution_token == TOKEN
        assert caught.value.container_name == f"agent-sandbox-{TOKEN}"
        assert caught.value.__suppress_context__
        assert [call[0] for call in calls] == (["create"] if stage == "create" else ["create", "inspect"])
    run(scenario)


@pytest.mark.parametrize("output", ["", "PRIVATE", "a" * 12, CID + "\nextra", None])
def test_invalid_create_response_stops_before_inspect(monkeypatch, output):
    async def scenario():
        calls = install(monkeypatch, create_output=output)
        with pytest.raises(service.SandboxCreationUnconfirmed):
            await service.create_and_confirm_sandbox(request=request(), execution_token=TOKEN)
        assert len(calls) == 1
    run(scenario)


@pytest.mark.parametrize("field", ["json", "id", "name", "token", "image", "running"])
def test_inspect_rejection_never_returns_identity(monkeypatch, field):
    async def scenario():
        data = payload()
        if field == "id":
            data[0]["Id"] = "c" * 64
        elif field == "name":
            data[0]["Name"] = "/other"
        elif field == "token":
            data[0]["Config"]["Labels"]["ai-agent-learning-lab.execution"] = "c" * 32
        elif field == "image":
            data[0]["Config"]["Image"] = "other"
        elif field == "running":
            data[0]["State"]["Running"] = True
        calls = install(monkeypatch, inspect_output="PRIVATE" if field == "json" else json.dumps(data))
        with pytest.raises(service.SandboxCreationUnconfirmed):
            await service.create_and_confirm_sandbox(request=request(), execution_token=TOKEN)
        assert [call[0] for call in calls] == ["create", "inspect"]
    run(scenario)


@pytest.mark.parametrize("stage", ["create", "inspect"])
def test_cancellation_propagates_after_dependency_cleanup(monkeypatch, stage):
    async def scenario():
        calls = install(monkeypatch)
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        async def blocked(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
        monkeypatch.setattr(service, f"{stage}_sandbox_container", blocked)
        task = asyncio.create_task(service.create_and_confirm_sandbox(request=request(), execution_token=TOKEN))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        assert len(calls) == (0 if stage == "create" else 1)
    run(scenario)


def test_create_adapter_passes_only_spec_arguments(monkeypatch):
    async def scenario():
        calls = []
        async def runner(args):
            calls.append(args)
            return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=CID + "\n")))
        monkeypatch.setattr(client, "_run_docker_client", runner)
        spec = build_sandbox_create_spec(request=request(), execution_token=TOKEN)
        assert await client.create_sandbox_container(spec=spec) == CID + "\n"
        assert calls == [spec.argv[1:]]
    run(scenario)


@pytest.mark.parametrize("argv", [(), ("docker",), ("other", "create"), ("docker", "run")])
def test_create_adapter_rejects_wrong_prefix(monkeypatch, argv):
    async def scenario():
        async def forbidden(args):
            pytest.fail("must not launch")
        monkeypatch.setattr(client, "_run_docker_client", forbidden)
        spec = build_sandbox_create_spec(request=request(), execution_token=TOKEN)
        with pytest.raises(ValueError):
            await client.create_sandbox_container(spec=replace(spec, argv=argv))
    run(scenario)


def test_create_adapter_rejects_wrong_type():
    async def scenario():
        with pytest.raises(TypeError):
            await client.create_sandbox_container(spec={})
    run(scenario)


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_CREATION_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("lost_response", [False, True])
def test_real_docker_create_confirm_and_cleanup(monkeypatch, lost_response):
    async def scenario():
        token = uuid4().hex
        command = request()
        spec = build_sandbox_create_spec(request=command, execution_token=token)
        actual_create = client.create_sandbox_container
        calls = []
        async def create(*, spec):
            calls.append(spec)
            output = await actual_create(spec=spec)
            if lost_response:
                # 已经真实创建成功后模拟响应丢失，不谎称是真实网络故障。
                raise client.DockerClientError("docker_client_timeout")
            return output
        monkeypatch.setattr(service, "create_sandbox_container", create)
        try:
            if lost_response:
                with pytest.raises(service.SandboxCreationUnconfirmed) as caught:
                    await service.create_and_confirm_sandbox(request=command, execution_token=token)
                assert caught.value.execution_token == token
            else:
                identity = await service.create_and_confirm_sandbox(request=command, execution_token=token)
                assert identity.execution_token == token
            assert len(calls) == 1
        finally:
            # 仅在严格核对本轮随机token、名称、镜像和created状态后，按完整ID清理。
            # 核对失败让验收失败并保留现场，不猜测性删除或强制停止其他容器。
            text = await client.inspect_sandbox_container(execution_token=token)
            container_id = json.loads(text)[0]["Id"]
            owned = confirm_created_sandbox_identity(container_id=container_id, inspect_stdout=text, spec=spec)
            await client._run_docker_client(("container", "rm", owned.container_id))
            remaining = await client._run_docker_client(("container", "ls", "--all", "--quiet", "--no-trunc",
                                                       "--filter", f"id={owned.container_id}"))
            assert remaining.streams.stdout.text == ""
    asyncio.run(asyncio.wait_for(scenario(), 45))
