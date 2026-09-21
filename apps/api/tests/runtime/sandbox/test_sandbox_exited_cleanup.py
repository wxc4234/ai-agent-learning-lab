"""已退出容器的显式清理：停止证据、删除回执、缺失查询与取消。"""

import asyncio
import json
import os
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_cleanup as service
from app.services.runtime.command.command_contracts import CommandRequest
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_stop import data


def payload():
    value = data("exited")
    value[0]["State"].update(ExitCode=7, OOMKilled=False, Error="")
    return value


def install(monkeypatch, *, value=None, failure=None, present=False, receipt=None):
    calls = []

    async def runner(args):
        calls.append(args)
        operation = args[1]
        if failure == operation:
            raise OSError("PRIVATE daemon error")
        text = {
            "inspect": json.dumps(payload() if value is None else value),
            "rm": CID + "\n" if receipt is None else receipt,
            "ls": CID + "\n" if present else "",
        }[operation]
        return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))

    monkeypatch.setattr(client, "_run_docker_client", runner)
    return calls


async def cleanup(**kwargs):
    return await service.cleanup_exited_sandbox(**{
        "request": request(), "execution_token": TOKEN, "expected_container_id": CID,
    } | kwargs)


@pytest.mark.parametrize("exit_code", [0, 7, 137])
def test_exited_cleanup_uses_full_id_and_not_exit_success(monkeypatch, exit_code):
    async def scenario():
        value = payload()
        value[0]["State"]["ExitCode"] = exit_code
        calls = install(monkeypatch, value=value)
        result = await cleanup()
        assert result.container_id == CID and result.execution_token == TOKEN
        assert calls == [
            ("container", "inspect", CID), ("container", "rm", CID),
            ("container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"id={CID}"),
        ]
        with pytest.raises(FrozenInstanceError):
            result.container_id = "b" * 64
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [
    ("Status", "created"), ("Status", "running"), ("Status", "paused"),
    ("Status", "dead"), ("Status", "restarting"), ("Status", None),
    ("Running", True), ("Running", 0), ("Running", None),
    ("Pid", 1), ("Pid", -1), ("Pid", False), ("Pid", "0"),
    ("Paused", True), ("Restarting", True), ("Dead", True),
    ("Paused", 0), ("Restarting", None), ("Dead", None),
])
def test_inconsistent_or_nonexited_state_never_deletes(monkeypatch, field, value):
    async def scenario():
        snapshot = payload()
        snapshot[0]["State"][field] = value
        calls = install(monkeypatch, value=snapshot)
        with pytest.raises(service.SandboxCleanupUnconfirmed):
            await cleanup()
        assert calls == [("container", "inspect", CID)]
    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["id", "name", "token", "role", "image"])
def test_identity_mismatch_never_deletes(monkeypatch, field):
    async def scenario():
        value = payload()
        if field == "id":
            value[0]["Id"] = "b" * 64
        elif field == "name":
            value[0]["Name"] = "/unrelated"
        elif field == "image":
            value[0]["Config"]["Image"] = "unapproved"
        else:
            label = "execution" if field == "token" else "role"
            value[0]["Config"]["Labels"][f"ai-agent-learning-lab.{label}"] = "wrong"
        calls = install(monkeypatch, value=value)
        with pytest.raises(service.SandboxCleanupUnconfirmed):
            await cleanup()
        assert len(calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, True, "", "a" * 12, "A" * 64, CID + "\n", "--force"])
def test_invalid_id_has_no_external_effect(monkeypatch, value):
    async def scenario():
        calls = install(monkeypatch)
        with pytest.raises(ValueError):
            await cleanup(expected_container_id=value)
        assert calls == []
    asyncio.run(scenario())


@pytest.mark.parametrize("options", [
    {"request": {}}, {"execution_token": "bad"},
    {"request": CommandRequest(argv=["relative"])},
    {"request": CommandRequest(argv=["/bin/true"], working_directory="src")},
])
def test_invalid_context_has_no_external_effect(monkeypatch, options):
    async def scenario():
        calls = install(monkeypatch)
        with pytest.raises((TypeError, ValueError)):
            await cleanup(**options)
        assert calls == []
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["inspect", "rm", "ls"])
def test_failure_does_not_retry_or_claim_absence(monkeypatch, stage):
    async def scenario():
        calls = install(monkeypatch, failure=stage)
        with pytest.raises(service.SandboxCleanupUnconfirmed) as caught:
            await cleanup()
        assert str(caught.value) == "Sandbox 清理结果未确认"
        assert caught.value.execution_token == TOKEN and caught.value.container_id == CID
        operations = ["inspect", "rm", "ls"]
        assert [args[1] for args in calls] == operations[:operations.index(stage) + 1]
    asyncio.run(scenario())


@pytest.mark.parametrize("receipt", ["", "b" * 64, CID + "\nextra"])
def test_wrong_remove_receipt_is_not_success(monkeypatch, receipt):
    async def scenario():
        calls = install(monkeypatch, receipt=receipt)
        with pytest.raises(service.SandboxCleanupUnconfirmed):
            await cleanup()
        assert [args[1] for args in calls] == ["inspect", "rm"]
    asyncio.run(scenario())


def test_remove_receipt_without_absence_is_unconfirmed(monkeypatch):
    async def scenario():
        calls = install(monkeypatch, present=True)
        with pytest.raises(service.SandboxCleanupUnconfirmed):
            await cleanup()
        assert len(calls) == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["inspect", "rm", "ls"])
def test_cancel_retains_identity_and_delete_attempt_without_followups(monkeypatch, stage):
    async def scenario():
        calls = install(monkeypatch)
        actual = client._run_docker_client
        entered, finished = asyncio.Event(), asyncio.Event()

        async def runner(args):
            if args[1] != stage:
                return await actual(args)
            calls.append(args)
            entered.set()
            try:
                await asyncio.Future()
            finally:
                finished.set()

        monkeypatch.setattr(client, "_run_docker_client", runner)
        task = asyncio.create_task(cleanup())
        try:
            await entered.wait()
            task.cancel()
            with pytest.raises(service.SandboxCleanupCancelled) as caught:
                await task
            assert isinstance(caught.value, asyncio.CancelledError)
            assert caught.value.container_id == CID and caught.value.execution_token == TOKEN
            assert caught.value.delete_attempted is (stage != "inspect")
            assert finished.is_set() and calls[-1][1] == stage
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_request_is_frozen_before_inspect_wait(monkeypatch):
    async def scenario():
        calls = install(monkeypatch)
        actual = client._run_docker_client
        command = request()

        async def runner(args):
            command.argv[:] = ["changed"]
            command.working_directory = "src"
            return await actual(args)

        monkeypatch.setattr(client, "_run_docker_client", runner)
        assert (await cleanup(request=command)).container_id == CID
        assert len(calls) == 3
    asyncio.run(scenario())


def test_created_entry_still_rejects_exited(monkeypatch):
    async def scenario():
        calls = install(monkeypatch)
        with pytest.raises(service.SandboxCleanupUnconfirmed):
            await service.cleanup_created_sandbox(request=request(), execution_token=TOKEN, expected_container_id=CID)
        assert len(calls) == 1
    asyncio.run(scenario())


@pytest.mark.skipif(os.environ.get("RUN_EXITED_CLEANUP_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("failure", [None, "lost_remove", "lost_query", "cancel_after_remove"])
def test_real_exited_cleanup_and_uncertain_results(monkeypatch, failure):
    from app.services.runtime.sandbox.sandbox_creation import create_and_confirm_sandbox
    from app.services.runtime.sandbox.sandbox_execution import execute_created_sandbox
    from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
    from app.services.runtime.sandbox.sandbox_stop import confirm_sandbox_state, stop_and_confirm_sandbox

    async def scenario():
        token = uuid4().hex
        command = CommandRequest(argv=["/usr/local/bin/python", "-c", "raise SystemExit(7)"])
        identity = await create_and_confirm_sandbox(request=command, execution_token=token)
        original_remove = service.remove_sandbox_container
        original_absent = service.is_sandbox_container_absent
        removals = []

        async def remove(*, container_id):
            removals.append(container_id)
            await original_remove(container_id=container_id)
            if failure == "lost_remove":
                raise client.DockerClientError("docker_client_timeout")
            if failure == "cancel_after_remove":
                raise asyncio.CancelledError()

        async def absent(*, container_id):
            if failure == "lost_query":
                raise client.DockerClientError("docker_client_failed")
            return await original_absent(container_id=container_id)

        try:
            result = await execute_created_sandbox(request=command, execution_token=token,
                                                   expected_container_id=identity.container_id)
            assert result.exit.exit_code == 7
            monkeypatch.setattr(service, "remove_sandbox_container", remove)
            monkeypatch.setattr(service, "is_sandbox_container_absent", absent)
            # 真实同一容器、错误token必须在rm之前拒绝。
            with pytest.raises(service.SandboxCleanupUnconfirmed):
                await service.cleanup_exited_sandbox(request=command, execution_token=uuid4().hex,
                                                     expected_container_id=identity.container_id)
            assert removals == []
            options = {"request": command, "execution_token": token, "expected_container_id": identity.container_id}
            if failure:
                error = service.SandboxCleanupCancelled if failure == "cancel_after_remove" else service.SandboxCleanupUnconfirmed
                with pytest.raises(error) as caught:
                    await service.cleanup_exited_sandbox(**options)
                assert caught.value.container_id == identity.container_id
                if failure == "cancel_after_remove":
                    assert caught.value.delete_attempted
            else:
                assert (await service.cleanup_exited_sandbox(**options)).container_id == identity.container_id
            assert removals == [identity.container_id]
            assert await original_absent(container_id=identity.container_id)
            with pytest.raises(service.SandboxCleanupUnconfirmed):
                await service.cleanup_exited_sandbox(**options)
            assert len(removals) == 1
        finally:
            if not await original_absent(container_id=identity.container_id):
                await stop_and_confirm_sandbox(request=command, execution_token=token,
                                               expected_container_id=identity.container_id)
                text = await client.inspect_sandbox_container_by_id(container_id=identity.container_id)
                state = confirm_sandbox_state(container_id=identity.container_id, inspect_stdout=text,
                    spec=build_sandbox_create_spec(request=command, execution_token=token))
                assert state.stopped
                await original_remove(container_id=identity.container_id)
            assert await original_absent(container_id=identity.container_id)
    asyncio.run(asyncio.wait_for(scenario(), 45))
