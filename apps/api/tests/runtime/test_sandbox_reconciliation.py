"""只读核对的成功、拒绝、取消及显式启用的真实 Docker 验收。"""

import asyncio
import json
import os
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from app.services.runtime import docker_client as client
from app.services.runtime import sandbox_reconciliation as service
from app.services.runtime.sandbox_creation import SandboxCreationUnconfirmed, create_and_confirm_sandbox
from app.services.runtime.sandbox_identity import SandboxIdentityError, recover_created_sandbox_identity
from tests.runtime.test_sandbox_creation import CID, TOKEN, payload, request
from app.services.runtime.sandbox_spec import build_sandbox_create_spec

SPEC = build_sandbox_create_spec(request=request(), execution_token=TOKEN)


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario(), 3))


def recover(text):
    return recover_created_sandbox_identity(inspect_stdout=text, spec=SPEC)


@pytest.mark.parametrize("known_id", [None, CID])
def test_success_is_exactly_one_inspect(monkeypatch, known_id):
    async def scenario():
        calls = []
        async def runner(arguments):
            calls.append(arguments)
            from types import SimpleNamespace
            return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=json.dumps(payload()))))
        # 保留真实 inspect 适配器，在最底层记录全部 Docker 操作。
        monkeypatch.setattr(client, "_run_docker_client", runner)
        identity = await service.reconcile_created_sandbox(
            request=request(), execution_token=TOKEN, expected_container_id=known_id)
        assert identity.container_id == CID
        assert identity.execution_token == TOKEN
        assert calls == [("container", "inspect", f"agent-sandbox-{TOKEN}")]
        with pytest.raises(FrozenInstanceError):
            identity.container_id = "c" * 64
    run(scenario)


@pytest.mark.parametrize("known_id", [None, CID])
@pytest.mark.parametrize("code", ["docker_client_failed", "docker_client_timeout", "docker_response_unusable"])
def test_query_failure_preserves_uncertainty(monkeypatch, known_id, code):
    async def scenario():
        calls = []
        async def inspect(**kwargs):
            calls.append(kwargs)
            raise client.DockerClientError(code)
        monkeypatch.setattr(service, "inspect_sandbox_container", inspect)
        with pytest.raises(SandboxCreationUnconfirmed) as caught:
            await service.reconcile_created_sandbox(request=request(), execution_token=TOKEN,
                                                    expected_container_id=known_id)
        assert str(caught.value) == "Sandbox 创建结果未确认"
        assert caught.value.execution_token == TOKEN
        assert caught.value.container_name == SPEC.container_name
        assert caught.value.__suppress_context__
        assert len(calls) == 1
    run(scenario)


@pytest.mark.parametrize("known_id", ["", CID + "\n", "a" * 12, "A" * 64, True, 1, b"a" * 64])
def test_bad_known_id_never_queries(monkeypatch, known_id):
    async def scenario():
        async def forbidden(**kwargs):
            pytest.fail("invalid ID must fail before inspect")
        monkeypatch.setattr(service, "inspect_sandbox_container", forbidden)
        with pytest.raises(ValueError):
            await service.reconcile_created_sandbox(request=request(), execution_token=TOKEN,
                                                    expected_container_id=known_id)
    run(scenario)


@pytest.mark.parametrize("kind", ["token", "request", "directory", "mutated"])
def test_invalid_original_context_never_queries(monkeypatch, kind):
    async def scenario():
        async def forbidden(**kwargs):
            pytest.fail("preflight must precede inspect")
        monkeypatch.setattr(service, "inspect_sandbox_container", forbidden)
        command, token = request(), TOKEN
        if kind == "token":
            token = "bad"
        elif kind == "request":
            command = {}
        elif kind == "directory":
            command.working_directory = "src"
        else:
            command.argv.clear()
        with pytest.raises((TypeError, ValueError)):
            await service.reconcile_created_sandbox(request=command, execution_token=token)
    run(scenario)


def test_known_id_mismatch_is_not_downgraded_to_discovery(monkeypatch):
    async def scenario():
        async def inspect(**kwargs):
            return json.dumps(payload(container_id="c" * 64))
        monkeypatch.setattr(service, "inspect_sandbox_container", inspect)
        with pytest.raises(SandboxCreationUnconfirmed):
            await service.reconcile_created_sandbox(request=request(), execution_token=TOKEN,
                                                    expected_container_id=CID)
        # ID未知时只能证明当前匹配目标，不能证明它从未被替换。
        identity = await service.reconcile_created_sandbox(request=request(), execution_token=TOKEN)
        assert identity.container_id == "c" * 64
    run(scenario)


@pytest.mark.parametrize("text", [None, b"[]", "", "PRIVATE", "[]", "{}", "[1]", "[{},{}]",
                                   "[" * 2000 + "]" * 2000, "x" * 65537,
                                   '[{"Id":"a","Id":"b"}]', '[{"x":NaN}]',
                                   '[{"nested":{"x":1,"x":2}}]'])
def test_recovery_strict_response_rejection(text):
    with pytest.raises(SandboxIdentityError, match="无法确认 Sandbox"):
        recover(text)


@pytest.mark.parametrize("field,value", [
    ("Id", None), ("Id", 1), ("Id", "a" * 12), ("Id", CID + "\n"),
    ("Name", "/other"), ("role", "other"), ("token", "c" * 32),
    ("Image", "other"), ("Status", "running"), ("Running", True),
    ("Running", 0), ("Pid", False), ("Pid", 1),
])
def test_recovered_candidate_must_pass_all_identity_rules(field, value):
    data = payload()
    if field in {"Id", "Name"}:
        data[0][field] = value
    elif field == "Image":
        data[0]["Config"][field] = value
    elif field in {"role", "token"}:
        label = "role" if field == "role" else "execution"
        data[0]["Config"]["Labels"][f"ai-agent-learning-lab.{label}"] = value
    else:
        data[0]["State"][field] = value
    with pytest.raises(SandboxIdentityError):
        recover(json.dumps(data))


def test_recovery_length_boundary_and_extra_fields():
    data = payload()
    data[0]["unused"] = "extra"
    text = json.dumps(data)
    assert recover(text + " " * (65536 - len(text))).container_id == CID
    with pytest.raises(SandboxIdentityError):
        recover(text + " " * (65537 - len(text)))
    with pytest.raises(TypeError):
        recover_created_sandbox_identity(inspect_stdout=text, spec={})


@pytest.mark.parametrize("known_id", [None, CID])
def test_parser_and_unexpected_errors_are_safe(monkeypatch, known_id):
    async def scenario():
        async def inspect(**kwargs):
            return "PRIVATE invalid JSON"
        monkeypatch.setattr(service, "inspect_sandbox_container", inspect)
        with pytest.raises(SandboxCreationUnconfirmed, match="^Sandbox 创建结果未确认$"):
            await service.reconcile_created_sandbox(request=request(), execution_token=TOKEN,
                                                    expected_container_id=known_id)
        async def broken(**kwargs):
            raise RuntimeError("PRIVATE")
        monkeypatch.setattr(service, "inspect_sandbox_container", broken)
        with pytest.raises(SandboxCreationUnconfirmed, match="^Sandbox 创建结果未确认$"):
            await service.reconcile_created_sandbox(request=request(), execution_token=TOKEN,
                                                    expected_container_id=known_id)
    run(scenario)


def test_cancel_propagates_and_dependency_finishes(monkeypatch):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def inspect(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
        monkeypatch.setattr(service, "inspect_sandbox_container", inspect)
        task = asyncio.create_task(service.reconcile_created_sandbox(request=request(), execution_token=TOKEN))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
    run(scenario)


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_RECONCILIATION_DOCKER") != "1", reason="explicit Docker opt-in")
def test_real_docker_readonly_reconcile_and_absent_target():
    async def scenario():
        token = uuid4().hex
        command = request()
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        try:
            for known_id in (None, identity.container_id):
                found = await service.reconcile_created_sandbox(
                    request=command, execution_token=token, expected_container_id=known_id)
                assert found == identity
            with pytest.raises(SandboxCreationUnconfirmed):
                await service.reconcile_created_sandbox(request=command, execution_token=token,
                                                        expected_container_id="f" * 64)
            # 错误核对不能删除或启动目标，重新读取仍应为相同created身份。
            assert await service.reconcile_created_sandbox(request=command, execution_token=token) == identity
        finally:
            text = await client.inspect_sandbox_container(execution_token=token)
            from app.services.runtime.sandbox_identity import confirm_created_sandbox_identity
            owned = confirm_created_sandbox_identity(container_id=identity.container_id, inspect_stdout=text,
                         spec=build_sandbox_create_spec(request=command, execution_token=token))
            await client._run_docker_client(("container", "rm", owned.container_id))
            remaining = await client._run_docker_client(("container", "ls", "--all", "--quiet", "--no-trunc",
                                                       "--filter", f"id={owned.container_id}"))
            assert remaining.streams.stdout.text == ""
        # 已知测试目标已删除，服务仍须保守返回未确认，不猜测daemon错误类型。
        with pytest.raises(SandboxCreationUnconfirmed):
            await service.reconcile_created_sandbox(request=command, execution_token=token)
    asyncio.run(asyncio.wait_for(scenario(), 45))
