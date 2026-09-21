"""请求能力快照、模型往返与请求绑定命令执行器。"""

import asyncio
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers.chat.chat_execution import ChatExecution
from app.schemas import ChatRequest
from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import FinalAnswer, ToolAction, run_agent_loop
from app.services.runtime.execution.command_recovery_store import CommandRecoveryStore
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.runtime.sandbox.sandbox_command import (
    SandboxCommandCancelled, SandboxCommandResult, SandboxCommandUnconfirmed,
)
from app.services.runtime.sandbox.sandbox_command_result import build_command_result
from app.services.runtime.sandbox.sandbox_cleanup import SandboxCleanupResult
from app.tools import run_command as adapter
from app.tools.context import ToolExecutionContext
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError, tools_for_execution
from tests.model.test_model_decision import build_text_response, build_tool_response
from tests.runtime.sandbox.test_sandbox_command_result import execution
from tests.tools.test_run_command import RECOVERY


CONTEXT = ToolExecutionContext(1, "PRIVATE-session", "PRIVATE-workspace", "PRIVATE-task")


@pytest.mark.parametrize("context", [None, CONTEXT])
def test_default_capability_never_contains_command(context):
    assert "run_command" not in {tool.name for tool in tools_for_execution(context=context)}
    assert "run_command" not in TOOL_REGISTRY


def test_requires_context_and_callable():
    with pytest.raises(ToolContextRequiredError):
        tools_for_execution(context=None, command_executor=lambda: None)
    with pytest.raises(TypeError):
        tools_for_execution(context=CONTEXT, command_executor=1)


def test_parallel_bindings_and_context_identity():
    async def scenario():
        async def first(**kwargs):
            await asyncio.sleep(0)
            return "first"
        async def second(**kwargs):
            return "second"
        other_context = replace(CONTEXT)
        left = tools_for_execution(context=CONTEXT, command_executor=first)[-1]
        right = tools_for_execution(context=other_context, command_executor=second)[-1]
        arguments = left.validate_arguments('{"argv":["/bin/true"]}')
        assert await asyncio.gather(left.execute_async(arguments, context=CONTEXT),
                                    right.execute_async(arguments, context=other_context)) == ["first", "second"]
        with pytest.raises(ToolContextRequiredError):
            await left.execute_async(arguments, context=other_context)
        schema = left.as_model_tool()
        assert "PRIVATE" not in json.dumps(schema)
        assert set(schema["function"]["parameters"]["properties"]) == {"argv", "working_directory"}
        assert "run_command" not in TOOL_REGISTRY
    asyncio.run(scenario())


@pytest.mark.parametrize("explicit", [False, True])
def test_empty_snapshot_disables_tools_but_none_keeps_default(explicit):
    async def decide(observations):
        if observations:
            return FinalAnswer(content="done")
        return ToolAction(tool_call_id="call", tool_name="calculate_rectangle_area", arguments='{"width":2,"height":3}')
    definitions = () if explicit else None
    result = asyncio.run(run_agent_loop(decide, tool_definitions=definitions))
    if explicit:
        assert result.observations[0].code == "unknown_tool"
    else:
        assert result.observations[0].result == "6"
    model = DeepSeekDecisionMaker(client=None, model="test", messages=[{"role": "user", "content": "test"}],
                                  tool_definitions=definitions)
    assert bool(model._tools) is (not explicit)


def test_duplicate_rejected_before_model_call():
    tool = tools_for_execution(context=None)[0]
    async def forbidden(observations):
        pytest.fail("duplicate capability must fail before model")
    with pytest.raises(ValueError):
        asyncio.run(run_agent_loop(forbidden, tool_definitions=(tool, tool)))


@pytest.mark.parametrize("outcome", ["success", "nonzero", "cleanup", "timeout", "cancel"])
def test_model_runtime_bound_owner_and_recovery(monkeypatch, outcome):
    async def scenario():
        owner = ChatExecution(user_id=1, body=ChatRequest(session_id=CONTEXT.conversation_id, prompt="test"),
                              threads=ExecutionThreads(), command_recovery_store=CommandRecoveryStore())
        async def created():
            return 1
        owner.creation = asyncio.create_task(created())
        await owner.creation
        command = build_command_result(execution(code=7 if outcome == "nonzero" else 0))
        recovery = replace(RECOVERY, phase="cleaning", command=command)
        async def sandbox(**kwargs):
            if outcome == "cleanup":
                raise SandboxCommandUnconfirmed(recovery=recovery)
            if outcome == "cancel":
                raise SandboxCommandCancelled(recovery=recovery)
            if outcome == "timeout":
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    raise SandboxCommandCancelled(recovery=recovery) from None
            return SandboxCommandResult(command=command, cleanup=SandboxCleanupResult(
                execution_token="PRIVATE", container_id="PRIVATE"))
        monkeypatch.setattr(adapter, "run_sandbox_command", sandbox)
        definitions = tools_for_execution(context=CONTEXT, command_executor=owner.execute_command)
        if outcome == "timeout":
            definitions = (*definitions[:-1], replace(definitions[-1], timeout_seconds=0.01))
        create = AsyncMock(side_effect=[build_tool_response(("call", "run_command", '{"argv":["/bin/true"]}')),
                                       build_text_response("done")])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        model = DeepSeekDecisionMaker(client=client, model="test", messages=[{"role": "user", "content": "test"}],
                                      tool_context=CONTEXT, tool_definitions=definitions)
        try:
            if outcome == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await run_agent_loop(model, tool_context=CONTEXT, tool_definitions=definitions)
                assert create.await_count == 1
            else:
                result = await run_agent_loop(model, tool_context=CONTEXT, tool_definitions=definitions)
                observation = result.observations[0]
                if outcome in ("success", "nonzero"):
                    assert json.loads(observation.result)["exit_code"] == command.exit_code
                else:
                    assert observation.code == ("tool_timeout" if outcome == "timeout" else "tool_execution_failed")
                assert "PRIVATE" not in str(observation)
                assert create.await_count == 2
            for call in create.call_args_list:
                assert "PRIVATE" not in json.dumps(call.kwargs)
                assert {item["function"]["name"] for item in call.kwargs["tools"]} == {item.name for item in definitions}
            record = owner.command_scope.journal.records[0]
            assert record.status == {"success": "completed", "nonzero": "completed", "cleanup": "unconfirmed",
                                     "timeout": "cancelled", "cancel": "cancelled"}[outcome]
            if outcome not in ("success", "nonzero"):
                assert record.recovery is recovery
        finally:
            owner.command_recovery_store.close_scope(owner.command_scope)
            await owner.threads.wait_closed()
    asyncio.run(scenario())


@pytest.mark.skipif(os.environ.get("RUN_REQUEST_COMMAND_DOCKER") != "1", reason="explicit Docker opt-in")
@pytest.mark.parametrize("code", [0, 7])
def test_real_docker_via_request_bound_tool(monkeypatch, code):
    from app.services.runtime.docker import docker_client
    async def scenario():
        owner = ChatExecution(user_id=1, body=ChatRequest(session_id=CONTEXT.conversation_id, prompt="test"),
                              threads=ExecutionThreads(), command_recovery_store=CommandRecoveryStore())
        async def created():
            return 1
        owner.creation = asyncio.create_task(created())
        await owner.creation
        actual = adapter.run_sandbox_command
        results = []
        async def observe(**kwargs):
            result = await actual(**kwargs)
            results.append(result)
            return result
        monkeypatch.setattr(adapter, "run_sandbox_command", observe)
        definition = tools_for_execution(context=CONTEXT, command_executor=owner.execute_command)[-1]
        arguments = definition.validate_arguments(json.dumps({"argv": ["/usr/local/bin/python", "-c",
                                                                         f"print('registered'); raise SystemExit({code})"]}))
        try:
            text = await definition.execute_async(arguments, context=CONTEXT)
            assert json.loads(text)["stdout"] == "registered\n" and json.loads(text)["exit_code"] == code
            assert await docker_client.is_sandbox_container_absent(container_id=results[0].cleanup.container_id)
        finally:
            if owner.command_scope is not None:
                owner.command_recovery_store.close_scope(owner.command_scope)
            await owner.threads.wait_closed()
    asyncio.run(asyncio.wait_for(scenario(), 45))
