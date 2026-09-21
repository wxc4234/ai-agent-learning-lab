"""原生异步工具分派、Observation及等待收尾；不开放生产命令工具。"""

import asyncio

import pytest

from app.services.runtime.agent.agent_runtime import (
    AgentLoopCompleted, FinalAnswer, ToolAction, ToolCallFailed, ToolCallSucceeded,
    run_agent_loop, stream_agent_loop,
)
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, GetCurrentTimeArguments, ToolDefinition


class RejectThreads(ExecutionThreads):
    async def run(self, *args, **kwargs):
        pytest.fail("async executor must not enter thread tracker")


async def decide(observations):
    if not observations:
        return ToolAction(tool_call_id="async-call", tool_name="async-test", arguments='{"utc_offset_hours":8}')
    return FinalAnswer(content="done")


def register(monkeypatch, executor, *, timeout=5, requires_context=False):
    monkeypatch.setitem(TOOL_REGISTRY, "async-test", ToolDefinition(
        name="async-test", description="test", arguments_model=GetCurrentTimeArguments,
        async_executor=executor, timeout_seconds=timeout, requires_context=requires_context,
    ))


async def consume(mode, *, events=None, **kwargs):
    if mode == "result":
        return await run_agent_loop(**kwargs)
    result = None
    async for event in stream_agent_loop(**kwargs):
        if events is not None:
            events.append(event)
        if isinstance(event, AgentLoopCompleted):
            result = event.result
    assert result is not None
    return result


@pytest.mark.parametrize("mode", ["result", "stream"])
@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize("outcome", ["success", "safe", "unknown", "bad_result"])
def test_async_protocol_and_no_threads(monkeypatch, mode, tracked, outcome):
    async def scenario():
        loop = asyncio.get_running_loop()
        calls, events = [], []

        async def executor(*, utc_offset_hours):
            assert asyncio.get_running_loop() is loop
            calls.append(utc_offset_hours)
            await asyncio.sleep(0)
            if outcome == "safe":
                raise SafeToolExecutionError("file_unavailable")
            if outcome == "unknown":
                raise RuntimeError("PRIVATE output")
            return {} if outcome == "bad_result" else "async result"

        async def forbidden(*args, **kwargs):
            pytest.fail("async executor must not use to_thread")

        monkeypatch.setattr(asyncio, "to_thread", forbidden)
        register(monkeypatch, executor)
        tracker = RejectThreads() if tracked else None
        try:
            result = await consume(mode, events=events, decide=decide, execution_threads=tracker)
            assert calls == [8] and result.status == "completed"
            observation = result.observations[0]
            assert observation.tool_call_id == "async-call" and observation.tool_name == "async-test"
            assert observation.duration_ms is not None and observation.duration_ms >= 0
            assert "PRIVATE" not in str(observation)
            if outcome == "success":
                assert observation.result == "async result"
            else:
                assert observation.code == "tool_execution_failed"
                assert observation.details == {"safe": "file_unavailable", "unknown": "RuntimeError",
                                               "bad_result": "TypeError"}[outcome]
            if mode == "stream":
                terminal = ToolCallSucceeded if outcome == "success" else ToolCallFailed
                assert sum(isinstance(event, terminal) for event in events) == 1
        finally:
            if tracker is not None:
                await tracker.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["result", "stream"])
@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_waits_for_async_finally_before_timeout_or_cancel(monkeypatch, mode, stop):
    async def scenario():
        entered, cleaning, release, finished = (asyncio.Event() for _ in range(4))
        events = []

        async def executor(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
                finished.set()

        register(monkeypatch, executor, timeout=0.03 if stop == "timeout" else 5)
        task = asyncio.create_task(consume(mode, events=events, decide=decide))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if stop == "cancel":
                task.cancel()
            await asyncio.wait_for(cleaning.wait(), 1)
            assert not task.done() and not finished.is_set()
            assert not any(isinstance(event, (ToolCallFailed, ToolCallSucceeded)) for event in events)
            release.set()
            if stop == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.observations[0].code == "tool_timeout"
                assert result.observations[0].duration_ms >= 0
            assert finished.is_set()
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 3))


@pytest.mark.parametrize("mode", ["result", "stream"])
@pytest.mark.parametrize("failure", ["arguments", "context"])
def test_preflight_never_starts_async_executor(monkeypatch, mode, failure):
    async def executor(**kwargs):
        pytest.fail("preflight must block executor")

    async def decision(observations):
        if observations:
            return FinalAnswer(content="rejected")
        return ToolAction(tool_call_id="async-call", tool_name="async-test",
                          arguments='{"utc_offset_hours":"8"}' if failure == "arguments" else '{"utc_offset_hours":8}')

    register(monkeypatch, executor, requires_context=True)
    result = asyncio.run(consume(mode, decide=decision, execution_threads=RejectThreads()))
    observation = result.observations[0]
    assert observation.duration_ms is None
    assert observation.code == ("invalid_tool_arguments" if failure == "arguments" else "tool_execution_failed")


@pytest.mark.parametrize("mode", ["result", "stream"])
def test_context_is_injected_from_server(monkeypatch, mode):
    context = ToolExecutionContext(user_id=1, conversation_id="c", workspace_id="w", task_id="t")
    async def executor(*, context: ToolExecutionContext, utc_offset_hours):
        assert context is expected and utc_offset_hours == 8
        return "context accepted"
    expected = context
    register(monkeypatch, executor, requires_context=True)
    result = asyncio.run(consume(mode, decide=decide, tool_context=context))
    assert result.observations[0].result == "context accepted"


@pytest.mark.parametrize("mode", ["result", "stream"])
def test_executor_cancellation_is_not_error_observation(monkeypatch, mode):
    async def executor(**kwargs):
        raise asyncio.CancelledError("internal cancellation")
    register(monkeypatch, executor)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(consume(mode, decide=decide))
