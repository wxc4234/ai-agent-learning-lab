"""请求绑定、再授权、能力快照与取消后实际线程收尾。"""

import asyncio
import json
from dataclasses import replace
from threading import Event, get_ident

import pytest

from app.config import settings
from app.routers.chat import chat_execution as owners
from app.schemas import ChatRequest
from app.services.chat import chat_service as service
from app.services.runtime.agent.agent_runtime import (
    AgentLoopCompleted,
    AgentLoopResult,
    FinalAnswer,
    ToolAction,
    ToolCallFailed,
    stream_agent_loop,
)
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.tools import vault_search as tool
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from tests.chat.test_chat_tool_context import (
    isolated_history_and_monitor,
    unit,
)
from tests.tools.test_vault_search_tool import result


__all__ = ["isolated_history_and_monitor", "unit"]
CONTEXT = ToolExecutionContext(1, "c", "w", "t")


@pytest.fixture
def binding(monkeypatch):
    calls = []
    monkeypatch.setattr(
        owners, "load_tool_execution_context", lambda **kwargs: replace(CONTEXT)
    )

    def read(**kwargs):
        calls.append(kwargs)
        return result()

    monkeypatch.setattr(tool, "search_vault_markdown", read)
    execution = owners.ChatExecution(
        user_id=1,
        body=ChatRequest(session_id="c", prompt="search"),
        threads=ExecutionThreads(),
    )
    yield execution, calls
    asyncio.run(execution.close())


def test_binding_is_lazy_and_rejects_an_equal_context_from_another_request(binding):
    execution, calls = binding
    definition = execution.bind_vault_search_tool(CONTEXT)
    assert not calls
    with pytest.raises(SafeToolExecutionError) as caught:
        definition.execute(
            tool.VaultSearchArguments(query="事务边界"), context=replace(CONTEXT)
        )
    assert caught.value.code == "vault_search_unavailable" and not calls
    assert (
        json.loads(
            definition.execute(
                tool.VaultSearchArguments(query="事务边界"),
                context=CONTEXT,
            )
        )["task_id"]
        == "t"
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        replace(CONTEXT, user_id=2),
        replace(CONTEXT, conversation_id="other"),
    ],
)
def test_binding_rejects_wrong_request_identity(binding, context):
    execution, calls = binding
    with pytest.raises(SafeToolExecutionError):
        execution.bind_vault_search_tool(context)
    assert not calls


@pytest.mark.parametrize(
    "field", ["user_id", "conversation_id", "workspace_id", "task_id"]
)
def test_reloaded_identity_change_stops_before_vault_io(binding, monkeypatch, field):
    execution, calls = binding
    definition = execution.bind_vault_search_tool(CONTEXT)
    current = replace(CONTEXT, **{field: 2 if field == "user_id" else "other"})
    monkeypatch.setattr(owners, "load_tool_execution_context", lambda **kwargs: current)
    with pytest.raises(SafeToolExecutionError) as caught:
        definition.execute(tool.VaultSearchArguments(query="事务边界"), context=CONTEXT)
    assert caught.value.code == "workspace_not_accessible" and not calls


def test_reauthorization_failure_is_safe(binding, monkeypatch):
    execution, calls = binding
    definition = execution.bind_vault_search_tool(CONTEXT)

    def fail(**kwargs):
        raise OSError("PRIVATE database")

    monkeypatch.setattr(owners, "load_tool_execution_context", fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        definition.execute(tool.VaultSearchArguments(query="事务边界"), context=CONTEXT)
    assert caught.value.code == "workspace_not_accessible" and not calls
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("stage", ["before", "authorization", "search"])
def test_closed_request_rejects_binding_execution_and_late_result(
    binding, monkeypatch, stage
):
    execution, calls = binding
    definition = execution.bind_vault_search_tool(CONTEXT)
    if stage == "before":
        asyncio.run(execution.close())
        with pytest.raises(SafeToolExecutionError):
            execution.bind_vault_search_tool(CONTEXT)
    elif stage == "authorization":

        def load(**kwargs):
            execution._command_closed = True
            return replace(CONTEXT)

        monkeypatch.setattr(owners, "load_tool_execution_context", load)
    else:

        def read(**kwargs):
            calls.append(kwargs)
            execution._command_closed = True
            return result()

        monkeypatch.setattr(tool, "search_vault_markdown", read)
    with pytest.raises(SafeToolExecutionError):
        definition.execute(tool.VaultSearchArguments(query="事务边界"), context=CONTEXT)
    assert len(calls) == (1 if stage == "search" else 0)


def test_model_and_runtime_share_vault_snapshot_without_mutating_history(
    unit, monkeypatch
):
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(
        service, "load_tool_execution_context", lambda **kwargs: CONTEXT
    )
    history = [
        {"role": "system", "content": "cached system"},
        {"role": "user", "content": "search"},
    ]
    snapshots = []
    definition = tool.make_vault_search_definition()

    async def prepare(**kwargs):
        return history, list(history)

    def provider(context):
        assert context is CONTEXT
        return definition

    def model(**kwargs):
        snapshots.append(kwargs["tool_definitions"])
        assert kwargs["tool_definitions"][-1] is definition
        assert (
            kwargs["messages"][0]["content"]
            == service.DEFAULT_SYSTEM_PROMPT
            + "\n\n"
            + service.VAULT_SEARCH_SYSTEM_PROMPT
        )
        return object()

    async def loop(decide, **kwargs):
        assert kwargs["tool_definitions"] is snapshots[0]
        assert kwargs["tool_context"] is CONTEXT
        yield AgentLoopCompleted(AgentLoopResult("completed", "done", 1, ()))

    monkeypatch.setattr(service, "_prepare_chat_messages", prepare)
    monkeypatch.setattr(service, "DeepSeekDecisionMaker", model)
    monkeypatch.setattr(service, "stream_agent_loop", loop)

    async def scenario():
        tracker, monitors = ExecutionThreads(), []
        try:
            events = [
                json.loads(line)
                async for line in service.stream_chat_reply(
                    user_id=1,
                    session_id="c",
                    prompt="search",
                    run_id=1,
                    execution_threads=tracker,
                    monitors=monitors,
                    vault_search_binding_provider=provider,
                )
            ]
            assert events[-1]["type"] == "RUN_FINISHED"
        finally:
            for monitor in monitors:
                monitor.cancel()
            await asyncio.gather(*monitors, return_exceptions=True)
            await tracker.wait_closed()

    asyncio.run(scenario())
    assert history[0] == {"role": "system", "content": "cached system"}


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_sync_vault_work_is_tracked_until_actual_thread_finishes(
    binding, monkeypatch, mode
):
    execution, _ = binding
    release, finished = Event(), Event()
    main_thread = get_ident()

    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def read(**kwargs):
            assert get_ident() != main_thread
            loop.call_soon_threadsafe(started.set)
            try:
                assert release.wait(5)
                return result()
            finally:
                finished.set()

        monkeypatch.setattr(tool, "search_vault_markdown", read)
        definition = replace(
            execution.bind_vault_search_tool(CONTEXT), timeout_seconds=0.02
        )

        async def decide(observations):
            if not observations:
                return ToolAction("vault", "search_vault", '{"query":"事务边界"}')
            return FinalAnswer("检索已处理")

        async def collect():
            return [
                event
                async for event in stream_agent_loop(
                    decide,
                    tool_context=CONTEXT,
                    tool_definitions=(definition,),
                    execution_threads=execution.threads,
                )
            ]

        task = asyncio.create_task(collect())
        closing = None
        try:
            await asyncio.wait_for(started.wait(), 3)
            if mode == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                events = await asyncio.wait_for(task, 3)
                errors = [
                    event for event in events if isinstance(event, ToolCallFailed)
                ]
                assert len(errors) == 1 and errors[0].observation.code == "tool_timeout"
            closing = asyncio.create_task(execution.close())
            # 排空由事件推进，不用固定睡眠猜测工作线程是否结束。
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert execution._command_closed and not closing.done()
            assert not finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if closing is not None:
                await closing
        assert finished.is_set()

    asyncio.run(scenario())
