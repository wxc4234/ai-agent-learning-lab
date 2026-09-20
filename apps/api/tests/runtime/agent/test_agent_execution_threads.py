"""真实工具执行与跟踪器、会话作用域之间的集成边界。"""

import asyncio

import pytest

from app.services.runtime.agent.agent_runtime import (
    AgentLoopCompleted,
    FinalAnswer,
    ToolAction,
    run_agent_loop,
    stream_agent_loop,
)
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.tools.registry import TOOL_REGISTRY, GetCurrentTimeArguments, ToolDefinition
from tests.runtime.execution import test_conversation_execution_scope as scope_tests
from tests.runtime.execution.test_conversation_execution_service import tokens
from tests.runtime.execution.test_execution_threads import ControlledWork, checkpoint


scope_target = scope_tests.scope_target
execution = scope_tests.execution


async def decide(observations):
    if not observations:
        return ToolAction(
            tool_call_id="tracked-call", tool_name="tracked-tool",
            arguments='{"utc_offset_hours": 8}',
        )
    return FinalAnswer(content="finished")


def register(monkeypatch, executor, timeout=5):
    monkeypatch.setitem(TOOL_REGISTRY, "tracked-tool", ToolDefinition(
        name="tracked-tool", description="测试工具", arguments_model=GetCurrentTimeArguments,
        executor=executor, timeout_seconds=timeout,
    ))


async def consume(mode, tracker, decision=decide):
    if mode == "result":
        return await run_agent_loop(decision, execution_threads=tracker)
    async for event in stream_agent_loop(decision, execution_threads=tracker):
        if isinstance(event, AgentLoopCompleted):
            return event.result
    raise AssertionError("missing terminal result")


@pytest.mark.parametrize("mode", ["result", "stream"])
@pytest.mark.parametrize("failure", [False, True])
def test_success_and_error_preserve_protocol_and_tracker_ownership(monkeypatch, mode, failure):
    async def scenario():
        tracker = ExecutionThreads()
        calls = []

        def executor(*, utc_offset_hours):
            calls.append(utc_offset_hours)
            if failure:
                raise ValueError("private executor detail")
            return "tool-result"

        register(monkeypatch, executor)
        try:
            result = await consume(mode, tracker)
            assert calls == [8]
            assert result.status == "completed"
            observation = result.observations[0]
            assert observation.tool_call_id == "tracked-call"
            assert observation.duration_ms is not None
            if failure:
                assert observation.code == "tool_execution_failed"
                assert observation.details == "ValueError"
                assert "private executor detail" not in str(observation)
            else:
                assert observation.result == "tool-result"
            # Runtime 不得替外层关闭共享跟踪器，后续数据库工作仍可登记。
            assert await tracker.run(lambda: 42) == 42
        finally:
            await tracker.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["result", "stream"])
@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_abandoned_tool_is_still_tracked(monkeypatch, mode, stop):
    async def scenario():
        tracker = ExecutionThreads()
        work = ControlledWork()
        register(monkeypatch, lambda **kwargs: work(), timeout=0.1 if stop == "timeout" else 5)
        task = asyncio.create_task(consume(mode, tracker))
        try:
            await asyncio.wait_for(work.started.wait(), 2)
            if stop == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await asyncio.wait_for(task, 2)
                assert result.observations[0].code == "tool_timeout"
            assert not work.finished.is_set()
            closing = asyncio.create_task(tracker.wait_closed())
            await checkpoint()
            assert not closing.done()
            work.release.set()
            await asyncio.wait_for(closing, 2)
            assert work.finished.is_set()
        finally:
            work.release.set()
            await tracker.wait_closed()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("arguments,tool_name,code", [
    ('{"utc_offset_hours": "8"}', "tracked-tool", "invalid_tool_arguments"),
    ('{"utc_offset_hours": 8}', "unknown-tool", "unknown_tool"),
])
def test_rejected_tool_never_reaches_tracker(monkeypatch, arguments, tool_name, code):
    class RejectInvocation(ExecutionThreads):
        async def run(self, *args, **kwargs):
            pytest.fail("untrusted tool must not reach execution")

    async def decision(observations):
        if not observations:
            return ToolAction(tool_call_id="invalid-call", tool_name=tool_name, arguments=arguments)
        return FinalAnswer(content="rejected")

    register(monkeypatch, lambda **kwargs: "unexpected")

    async def scenario():
        tracker = RejectInvocation()
        try:
            result = await consume("result", tracker, decision)
            assert result.observations[0].code == code
        finally:
            await tracker.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_real_slot_remains_until_actual_tool_finishes(engine, execution, monkeypatch, stop):
    async def scenario():
        work = ControlledWork()
        loop_returned = asyncio.Event()
        register(monkeypatch, lambda **kwargs: work(), timeout=0.1 if stop == "timeout" else 5)

        async def body():
            async with execution() as tracker:
                result = await run_agent_loop(decide, execution_threads=tracker)
                assert result.observations[0].code == "tool_timeout"
                loop_returned.set()

        task = asyncio.create_task(body())
        try:
            await asyncio.wait_for(work.started.wait(), 2)
            original = tokens(engine)
            assert len(original) == 1
            if stop == "cancel":
                task.cancel()
            else:
                await asyncio.wait_for(loop_returned.wait(), 2)
            await checkpoint()
            assert not task.done()
            assert tokens(engine) == original
            assert not work.finished.is_set()
            work.release.set()
            if stop == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            else:
                await asyncio.wait_for(task, 3)
            assert work.finished.is_set()
            assert tokens(engine) == []
        finally:
            work.release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
