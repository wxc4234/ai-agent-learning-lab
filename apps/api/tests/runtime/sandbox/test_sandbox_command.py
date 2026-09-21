"""统一入口的阶段证据、请求隔离和取消；真实Docker验证显式启用。"""

import asyncio
import os
import re
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox import sandbox_command as service
from app.services.runtime.sandbox.sandbox_creation import SandboxCreationUnconfirmed
from app.services.runtime.sandbox.sandbox_cleanup import SandboxCleanupUnconfirmed
from tests.runtime.sandbox.test_sandbox_command_result import execution


CID = "a" * 64


def request():
    return CommandRequest(argv=["/usr/local/bin/python", "-c", "print('hello')"])


def install(monkeypatch, *, code=0, failure=None, error=None, mutate=None):
    calls = []
    tokens = []
    actual_adapter = service.build_command_result

    async def create(**kwargs):
        calls.append("creating")
        tokens.append(kwargs["execution_token"])
        if mutate:
            mutate()
        assert kwargs["request"].argv == request().argv
        if failure == "creating":
            raise error
        return SimpleNamespace(container_id=CID)

    async def execute(**kwargs):
        calls.append("executing")
        assert kwargs["request"].argv == request().argv
        assert kwargs["execution_token"] == tokens[-1]
        assert kwargs["expected_container_id"] == CID
        if failure == "executing":
            raise error
        return execution(code=code)

    def adapt(value):
        calls.append("adapting")
        if failure == "adapting":
            raise error
        return actual_adapter(value)

    async def cleanup(**kwargs):
        calls.append("cleaning")
        assert kwargs["request"].argv == request().argv
        assert kwargs["execution_token"] == tokens[-1]
        assert kwargs["expected_container_id"] == CID
        if failure == "cleaning":
            raise error
        return service.SandboxCleanupResult(execution_token=tokens[-1], container_id=CID)

    monkeypatch.setattr(service, "create_and_confirm_sandbox", create)
    monkeypatch.setattr(service, "execute_created_sandbox", execute)
    monkeypatch.setattr(service, "build_command_result", adapt)
    monkeypatch.setattr(service, "cleanup_exited_sandbox", cleanup)
    return calls, tokens


@pytest.mark.parametrize("code", [0, 7, 137, 255])
def test_order_cleanup_independent_of_exit_success(monkeypatch, code):
    calls, tokens = install(monkeypatch, code=code)
    result = asyncio.run(service.run_sandbox_command(request=request()))
    assert calls == ["creating", "executing", "adapting", "cleaning"]
    assert result.command.exit_code == code
    assert result.command.succeeded is (code == 0)
    assert result.cleanup.execution_token == tokens[0]
    assert re.fullmatch(r"[0-9a-f]{32}", tokens[0])
    assert result.command.stdout == "你好\n"
    assert result.command.duration_ms == 123
    with pytest.raises(FrozenInstanceError):
        result.cleanup = None


@pytest.mark.parametrize("phase", ["creating", "executing", "adapting", "cleaning"])
@pytest.mark.parametrize("cancel", [False, True])
def test_unexpected_failure_or_cancel_preserves_known_facts(monkeypatch, phase, cancel):
    error = asyncio.CancelledError("PRIVATE") if cancel else OSError("PRIVATE")
    calls, tokens = install(monkeypatch, failure=phase, error=error)
    with pytest.raises(service.SandboxCommandCancelled if cancel else service.SandboxCommandUnconfirmed) as caught:
        asyncio.run(service.run_sandbox_command(request=request()))
    recovery = caught.value.recovery
    assert recovery.phase == phase
    assert recovery.execution_token == tokens[0]
    assert recovery.container_name == f"agent-sandbox-{tokens[0]}"
    assert recovery.container_id == (None if phase == "creating" else CID)
    assert recovery.start_attempted is (True if phase in ("adapting", "cleaning") else None)
    assert recovery.stop_confirmed is recovery.start_attempted
    assert recovery.delete_attempted is None
    assert (recovery.command is not None) is (phase == "cleaning")
    assert calls == ["creating", "executing", "adapting", "cleaning"][:calls.index(phase) + 1]
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__
    with pytest.raises(FrozenInstanceError):
        recovery.phase = "creating"


@pytest.mark.parametrize("start,stop", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("reason", ["timed_out", "execution_failed", "cancel"])
def test_execution_evidence_does_not_trigger_delete(monkeypatch, start, stop, reason):
    facts = {"execution_token": "b" * 32, "container_id": CID,
             "start_attempted": start, "stop_confirmed": stop}
    error = (service.SandboxExecutionCancelled(**facts) if reason == "cancel"
             else service.SandboxExecutionUnconfirmed(**facts, reason=reason))
    calls, _ = install(monkeypatch, failure="executing", error=error)
    with pytest.raises(service.SandboxCommandCancelled if reason == "cancel" else service.SandboxCommandUnconfirmed) as caught:
        asyncio.run(service.run_sandbox_command(request=request()))
    recovery = caught.value.recovery
    assert recovery.start_attempted is start and recovery.stop_confirmed is stop
    assert recovery.execution_reason == (None if reason == "cancel" else reason)
    assert recovery.command is None and calls == ["creating", "executing"]


@pytest.mark.parametrize("attempted", [False, True])
def test_cleanup_cancel_keeps_command_and_attempt(monkeypatch, attempted):
    error = service.SandboxCleanupCancelled(execution_token="b" * 32, container_id=CID,
                                            delete_attempted=attempted)
    install(monkeypatch, failure="cleaning", error=error)
    with pytest.raises(service.SandboxCommandCancelled) as caught:
        asyncio.run(service.run_sandbox_command(request=request()))
    assert caught.value.recovery.command.succeeded
    assert caught.value.recovery.delete_attempted is attempted


@pytest.mark.parametrize("phase", ["creating", "cleaning"])
def test_known_unconfirmed_errors_do_not_invent_missing_evidence(monkeypatch, phase):
    error = (SandboxCreationUnconfirmed(execution_token="b" * 32, container_name="private")
             if phase == "creating" else SandboxCleanupUnconfirmed(execution_token="b" * 32, container_id=CID))
    install(monkeypatch, failure=phase, error=error)
    with pytest.raises(service.SandboxCommandUnconfirmed) as caught:
        asyncio.run(service.run_sandbox_command(request=request()))
    assert caught.value.recovery.phase == phase
    assert caught.value.recovery.delete_attempted is None


def test_request_copy_and_unique_execution_identity(monkeypatch):
    original = request()
    _, tokens = install(monkeypatch, mutate=lambda: original.argv.clear())
    asyncio.run(service.run_sandbox_command(request=original))
    asyncio.run(service.run_sandbox_command(request=request()))
    assert len(set(tokens)) == 2


@pytest.mark.parametrize("invalid", [None, {}, CommandRequest(argv=["relative"]),
                                    CommandRequest(argv=["/bin/true"], working_directory="subdir")])
def test_preflight_has_no_external_calls(monkeypatch, invalid):
    calls, _ = install(monkeypatch)
    with pytest.raises((TypeError, ValueError)):
        asyncio.run(service.run_sandbox_command(request=invalid))
    assert calls == []


@pytest.mark.parametrize("phase", ["creating", "executing", "cleaning"])
def test_actual_task_cancellation_waits_for_child_finally(monkeypatch, phase):
    async def scenario():
        calls, _ = install(monkeypatch)
        entered, finished = asyncio.Event(), asyncio.Event()

        async def blocked(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

        names = {"creating": "create_and_confirm_sandbox", "executing": "execute_created_sandbox",
                 "cleaning": "cleanup_exited_sandbox"}
        monkeypatch.setattr(service, names[phase], blocked)
        task = asyncio.create_task(service.run_sandbox_command(request=request()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(service.SandboxCommandCancelled) as caught:
                await task
            assert finished.is_set() and task.cancelled()
            assert caught.value.recovery.phase == phase
            assert (caught.value.recovery.command is not None) is (phase == "cleaning")
            assert "cleaning" not in calls
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 2))


@pytest.mark.skipif(os.environ.get("RUN_SANDBOX_COMMAND_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("code", [0, 7])
def test_real_create_execute_cleanup(monkeypatch, code):
    from app.services.runtime.docker import docker_client as client
    from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox

    async def scenario():
        command = CommandRequest(argv=["/usr/local/bin/python", "-c",
                                       f"print('unified'); raise SystemExit({code})"])
        identities = []
        actual_create = service.create_and_confirm_sandbox

        async def create(**kwargs):
            identity = await actual_create(**kwargs)
            identities.append(identity)
            return identity

        monkeypatch.setattr(service, "create_and_confirm_sandbox", create)
        try:
            result = await service.run_sandbox_command(request=command)
            assert result.command.stdout == "unified\n"
            assert result.command.exit_code == code and result.command.succeeded is (code == 0)
            assert result.cleanup.container_id == identities[0].container_id
            assert await client.is_sandbox_container_absent(container_id=result.cleanup.container_id)
        finally:
            # 只回收本轮确认过身份的目标，生产入口仍不做失败自动删除。
            for identity in identities:
                if not await client.is_sandbox_container_absent(container_id=identity.container_id):
                    await stop_and_confirm_sandbox(request=command, execution_token=identity.execution_token,
                                                   expected_container_id=identity.container_id)
                    await service.cleanup_exited_sandbox(request=command, execution_token=identity.execution_token,
                                                         expected_container_id=identity.container_id)
                assert await client.is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 45))
