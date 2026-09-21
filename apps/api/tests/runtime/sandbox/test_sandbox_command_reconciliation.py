"""失败命令核对只允许查询，状态快照不改变历史结果。"""

import asyncio
import json
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_command_reconciliation as service
from app.services.runtime.sandbox.sandbox_command import SandboxCommandRecovery
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_stop import data


RECOVERY = SandboxCommandRecovery(execution_token=TOKEN, container_name=f"agent-sandbox-{TOKEN}",
                                  phase="executing", container_id=CID)


def install(monkeypatch, *, status="created", absent=False, failure=None, mutate=None):
    calls = []

    async def runner(arguments):
        calls.append(arguments)
        # 最底层白名单确保没有启动、停止、删除、创建或其他写操作。
        assert arguments[:2] in (("container", "ls"), ("container", "inspect"))
        if failure == arguments[1]:
            raise OSError("PRIVATE")
        if arguments[1] == "ls":
            text = "" if absent else CID + "\n"
        else:
            value = data(status)
            if mutate:
                mutate(value)
            text = json.dumps(value)
        return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))

    monkeypatch.setattr(client, "_run_docker_client", runner)
    return calls


def run(recovery=RECOVERY):
    return asyncio.run(service.reconcile_sandbox_command(request=request(), recovery=recovery))


@pytest.mark.parametrize("status", ["created", "running", "exited"])
@pytest.mark.parametrize("phase", ["creating", "executing", "adapting", "cleaning"])
def test_current_state_not_inferred_from_phase(monkeypatch, status, phase):
    calls = install(monkeypatch, status=status)
    recovery = replace(RECOVERY, phase=phase, stop_confirmed=True)
    result = run(recovery)
    assert result.status == status and result.identity.container_id == CID
    assert result.recovery is recovery and recovery.stop_confirmed is True
    assert len(calls) == 2 and calls[-1] == ("container", "inspect", CID)
    with pytest.raises(FrozenInstanceError):
        result.status = "absent"


def test_absent_only_from_successful_list(monkeypatch):
    calls = install(monkeypatch, absent=True)
    result = run()
    assert result.status == "absent" and result.identity is None
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["ls", "inspect"])
def test_query_failure_does_not_become_absent(monkeypatch, failure):
    calls = install(monkeypatch, failure=failure)
    with pytest.raises(service.SandboxCommandReconciliationUnconfirmed) as caught:
        run()
    assert caught.value.recovery is RECOVERY
    assert "PRIVATE" not in str(caught.value)
    assert len(calls) == (1 if failure == "ls" else 2)


@pytest.mark.parametrize("status", ["created", "running", "exited"])
def test_unknown_id_reuses_created_only_reconciliation(monkeypatch, status):
    calls = install(monkeypatch, status=status)
    recovery = replace(RECOVERY, phase="creating", container_id=None)
    if status == "created":
        assert run(recovery).identity.container_id == CID
        assert len(calls) == 2
    else:
        with pytest.raises(service.SandboxCommandReconciliationUnconfirmed):
            run(recovery)
        assert len(calls) == 1
    assert calls[0] == ("container", "inspect", f"agent-sandbox-{TOKEN}")


@pytest.mark.parametrize("kind", ["id", "token", "name", "paused", "pid"])
def test_identity_and_inconsistent_state_rejected(monkeypatch, kind):
    def mutate(value):
        if kind == "id":
            value[0]["Id"] = "c" * 64
        elif kind == "token":
            value[0]["Config"]["Labels"]["ai-agent-learning-lab.execution"] = "c" * 32
        elif kind == "name":
            value[0]["Name"] = "/other"
        elif kind == "paused":
            value[0]["State"]["Paused"] = True
        else:
            value[0]["State"]["Pid"] = True
    install(monkeypatch, mutate=mutate)
    with pytest.raises(service.SandboxCommandReconciliationUnconfirmed):
        run()


@pytest.mark.parametrize("changes", [
    {"container_id": "short"}, {"container_id": "A" * 64}, {"container_id": True},
    {"container_id": None}, {"container_name": "other"}, {"execution_token": "bad"},
    {"phase": "invalid"},
])
def test_invalid_evidence_never_queries(monkeypatch, changes):
    calls = install(monkeypatch)
    with pytest.raises(ValueError):
        run(replace(RECOVERY, **changes))
    assert not calls


def test_cancel_preserves_evidence_and_joins_query(monkeypatch):
    async def scenario():
        entered, finished = asyncio.Event(), asyncio.Event()

        async def absent(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

        monkeypatch.setattr(service, "is_sandbox_container_absent", absent)
        task = asyncio.create_task(service.reconcile_sandbox_command(request=request(), recovery=RECOVERY))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(service.SandboxCommandReconciliationCancelled) as caught:
                await task
            assert caught.value.recovery is RECOVERY and finished.is_set() and task.cancelled()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("unknown", [False, True])
def test_caller_request_mutation_does_not_change_snapshot_context(monkeypatch, unknown):
    command = request()
    original = command.argv.copy()
    recovery = replace(RECOVERY, phase="creating", container_id=None) if unknown else RECOVERY
    install(monkeypatch)
    actual = service.reconcile_created_sandbox if unknown else service.is_sandbox_container_absent

    async def boundary(**kwargs):
        command.argv.clear()
        if unknown:
            assert kwargs["request"].argv == original
        return await actual(**kwargs)

    monkeypatch.setattr(service, "reconcile_created_sandbox" if unknown else "is_sandbox_container_absent", boundary)
    result = asyncio.run(service.reconcile_sandbox_command(request=command, recovery=recovery))
    assert result.status == "created" and not command.argv


@pytest.mark.parametrize("kind", ["request", "recovery", "mutated_request"])
def test_invalid_input_types_and_mutated_request(monkeypatch, kind):
    calls = install(monkeypatch)
    command = request()
    if kind == "mutated_request":
        command.argv.clear()
    with pytest.raises((TypeError, ValueError)):
        asyncio.run(service.reconcile_sandbox_command(
            request={} if kind == "request" else command,
            recovery={} if kind == "recovery" else RECOVERY,
        ))
    assert not calls


def test_real_readonly_lifecycle():
    import os
    from uuid import uuid4

    from app.services.runtime.command.command_contracts import CommandRequest
    from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
    from app.services.runtime.sandbox.sandbox_cleanup import cleanup_created_sandbox, cleanup_exited_sandbox
    from app.services.runtime.sandbox.sandbox_stop import stop_and_confirm_sandbox

    if os.environ.get("RUN_SANDBOX_COMMAND_RECONCILIATION_DOCKER") != "1":
        pytest.skip("explicit Docker opt-in")

    async def scenario():
        command = CommandRequest(argv=["/usr/local/bin/python", "-c", "import time; time.sleep(60)"])
        token = uuid4().hex
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        recovery = SandboxCommandRecovery(execution_token=token, container_name=identity.container_name,
                                          container_id=identity.container_id, phase="executing")
        try:
            for evidence in (recovery, replace(recovery, phase="creating", container_id=None)):
                snapshot = await service.reconcile_sandbox_command(request=command, recovery=evidence)
                assert snapshot.status == "created" and snapshot.identity == identity
            await client.start_sandbox_container(container_id=identity.container_id)
            snapshot = await service.reconcile_sandbox_command(request=command, recovery=recovery)
            assert snapshot.status == "running"
            await stop_and_confirm_sandbox(request=command, execution_token=token,
                                           expected_container_id=identity.container_id)
            snapshot = await service.reconcile_sandbox_command(request=command, recovery=recovery)
            assert snapshot.status == "exited"
        finally:
            state = await stop_and_confirm_sandbox(request=command, execution_token=token,
                                                   expected_container_id=identity.container_id)
            cleanup = cleanup_created_sandbox if state.status == "created" else cleanup_exited_sandbox
            await cleanup(request=command, execution_token=token, expected_container_id=identity.container_id)
        snapshot = await service.reconcile_sandbox_command(request=command, recovery=recovery)
        assert snapshot.status == "absent" and snapshot.identity is None
        assert await client.is_sandbox_container_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 45))
