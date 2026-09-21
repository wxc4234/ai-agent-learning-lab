"""受限命令适配及安全错误，不调用Docker或模型。"""

import asyncio
import json
from dataclasses import replace

import pytest

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox.sandbox_command import (
    SandboxCommandCancelled, SandboxCommandRecovery, SandboxCommandResult, SandboxCommandUnconfirmed,
)
from app.services.runtime.sandbox.sandbox_cleanup import SandboxCleanupResult
from app.services.runtime.sandbox.sandbox_command_result import build_command_result
from app.tools import run_command as tool
from app.tools.errors import SafeToolExecutionError
from tests.runtime.sandbox.test_sandbox_command_result import execution


async def invoke_command(**kwargs):
    # 适配器现在要求拥有者显式提供记录容器，旧协议测试也走新入口。
    kwargs.setdefault("recovery_journal", tool.CommandRecoveryJournal())
    return await tool.run_command(**kwargs)


RECOVERY = SandboxCommandRecovery(execution_token="PRIVATE", container_name="PRIVATE",
                                  container_id="PRIVATE", phase="executing")


@pytest.mark.parametrize("code", [0, 7, 137])
@pytest.mark.parametrize("truncated", [False, True])
def test_public_result_only_and_nonzero_is_not_tool_error(monkeypatch, code, truncated):
    command = build_command_result(execution(code=code)).model_copy(update={"stdout_truncated": truncated})

    async def execute(*, request):
        assert request.argv == ["/bin/echo", "a b", ";"]
        return SandboxCommandResult(command=command, cleanup=SandboxCleanupResult(
            execution_token="PRIVATE", container_id="PRIVATE"))

    monkeypatch.setattr(tool, "run_sandbox_command", execute)
    text = asyncio.run(invoke_command(argv=["/bin/echo", "a b", ";"]))
    assert json.loads(text) == command.model_dump()
    assert "PRIVATE" not in text


@pytest.mark.parametrize("phase,reason,has_command,expected", [
    ("creating", None, False, "command_creation_unconfirmed"),
    ("executing", "timed_out", False, "command_timeout_unconfirmed"),
    ("executing", "execution_failed", False, "command_execution_unconfirmed"),
    ("executing", None, False, "command_execution_unconfirmed"),
    ("adapting", None, False, "command_result_unavailable"),
    ("cleaning", None, True, "command_cleanup_unconfirmed"),
    ("cleaning", None, False, "command_result_unavailable"),
])
def test_safe_mapping_preserves_internal_recovery(monkeypatch, phase, reason, has_command, expected):
    recovery = replace(RECOVERY, phase=phase, execution_reason=reason,
                       command=build_command_result(execution()) if has_command else None)

    async def execute(**kwargs):
        raise SandboxCommandUnconfirmed(recovery=recovery)

    monkeypatch.setattr(tool, "run_sandbox_command", execute)
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke_command(argv=["/bin/true"]))
    assert caught.value.code == expected and caught.value.recovery is recovery
    assert "PRIVATE" not in str(caught.value) and "PRIVATE" not in caught.value.message
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("kwargs", [
    {"argv": []}, {"argv": "echo hi"}, {"argv": [1]}, {"argv": ["relative"]},
    {"argv": ["/bin/true"], "working_directory": "src"},
    {"argv": ["/bin/true"], "working_directory": "../x"},
    {"argv": ["/bin/true\x00"]},
])
def test_invalid_input_never_executes(monkeypatch, kwargs):
    async def forbidden(**kwargs):
        pytest.fail("invalid input must not execute")
    monkeypatch.setattr(tool, "run_sandbox_command", forbidden)
    with pytest.raises(tool.CommandToolExecutionError) as caught:
        asyncio.run(invoke_command(**kwargs))
    assert caught.value.code == "command_request_rejected" and caught.value.recovery is None


def test_unknown_error_is_safe(monkeypatch):
    async def execute(**kwargs):
        raise RuntimeError("PRIVATE docker path and output")
    monkeypatch.setattr(tool, "run_sandbox_command", execute)
    with pytest.raises(tool.CommandToolExecutionError) as caught:
        asyncio.run(invoke_command(argv=["/bin/true"]))
    assert caught.value.code == "command_result_unavailable" and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("specialized", [False, True])
def test_cancellation_propagates_unchanged(monkeypatch, specialized):
    error = SandboxCommandCancelled(recovery=RECOVERY) if specialized else asyncio.CancelledError()
    async def execute(**kwargs):
        raise error
    monkeypatch.setattr(tool, "run_sandbox_command", execute)
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(invoke_command(argv=["/bin/true"]))
    assert caught.value is error


def test_schema_is_existing_contract_and_private_options_forbidden():
    assert tool.RunCommandArguments is CommandRequest
    schema = tool.RunCommandArguments.model_json_schema()
    assert set(schema["properties"]) == {"argv", "working_directory"}
    assert schema["additionalProperties"] is False
    for key in ("execution_token", "context", "image", "timeout", "env", "mounts", "recovery_journal"):
        with pytest.raises(ValueError):
            tool.RunCommandArguments.model_validate({"argv": ["/bin/true"], key: "PRIVATE"})
