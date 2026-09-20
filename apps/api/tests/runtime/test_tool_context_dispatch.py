"""工具上下文：显式透传、执行前拒绝与并发隔离。"""

import asyncio
from threading import Barrier

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.services.runtime import agent_runtime as runtime
from app.services.runtime.execution_threads import ExecutionThreads
from app.tools.context import ToolExecutionContext
from app.tools.registry import ToolContextRequiredError, ToolDefinition, TOOL_REGISTRY


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    relative_path: str


def context(number=1):
    return ToolExecutionContext(
        user_id=number, conversation_id=f"c{number}",
        workspace_id=f"w{number}", task_id=f"t{number}",
    )


def definition(executor, model=Arguments, required=True):
    return ToolDefinition(
        name="context_test", description="test", arguments_model=model,
        executor=executor, requires_context=required,
    )


async def decide(observations):
    if not observations:
        return runtime.ToolAction("call", "context_test", '{"relative_path":"README.md"}')
    return runtime.FinalAnswer("done")


async def run(mode, **kwargs):
    if mode == "run":
        return await runtime.run_agent_loop(decide, **kwargs)
    events = [event async for event in runtime.stream_agent_loop(decide, **kwargs)]
    assert isinstance(events[0], runtime.ToolCallStarted)
    assert isinstance(events[-1], runtime.AgentLoopCompleted)
    return events[-1].result


@pytest.mark.parametrize("value", [None, {}, "forged"])
def test_direct_execute_requires_real_context(value):
    tool = definition(lambda **kwargs: pytest.fail("executor must not run"))
    with pytest.raises(ToolContextRequiredError):
        tool.execute(Arguments(relative_path="file"), context=value)


@pytest.mark.parametrize("mode", ["run", "stream"])
@pytest.mark.parametrize("value", [None, {}])
def test_missing_context_rejected_before_thread(monkeypatch, mode, value):
    class ForbiddenTracker:
        async def run(self, *args, **kwargs):
            pytest.fail("must not start thread")

    monkeypatch.setitem(TOOL_REGISTRY, "context_test", definition(lambda **kwargs: pytest.fail("executor")))
    result = asyncio.run(run(mode, tool_context=value, execution_threads=ForbiddenTracker()))
    observation = result.observations[0]
    assert isinstance(observation, runtime.ToolErrorObservation)
    assert observation.code == "tool_execution_failed"
    assert observation.details == "tool_context_required"
    assert observation.duration_ms is None
    assert result.status == "completed"


@pytest.mark.parametrize("mode", ["run", "stream"])
@pytest.mark.parametrize("tracked", [True, False])
def test_context_identity_reaches_executor(monkeypatch, mode, tracked):
    trusted = context()
    seen = []

    def execute(*, context, relative_path):
        seen.append(context)
        assert relative_path == "README.md"
        return context.task_id

    monkeypatch.setitem(TOOL_REGISTRY, "context_test", definition(execute))

    async def scenario():
        tracker = ExecutionThreads() if tracked else None
        try:
            result = await run(mode, tool_context=trusted, execution_threads=tracker)
            assert result.observations[0].result == "t1"
        finally:
            if tracker is not None:
                await tracker.wait_closed()

    asyncio.run(scenario())
    assert len(seen) == 1 and seen[0] is trusted


def test_parallel_runs_keep_contexts_separate(monkeypatch):
    barrier = Barrier(2)
    first, second = context(1), context(2)

    def execute(*, context, relative_path):
        barrier.wait(timeout=3)
        return f"{context.user_id}:{context.task_id}"

    monkeypatch.setitem(TOOL_REGISTRY, "context_test", definition(execute))

    async def scenario():
        trackers = [ExecutionThreads(), ExecutionThreads()]
        try:
            results = await asyncio.gather(
                run("run", tool_context=first, execution_threads=trackers[0]),
                run("stream", tool_context=second, execution_threads=trackers[1]),
            )
            assert [result.observations[0].result for result in results] == ["1:t1", "2:t2"]
        finally:
            await asyncio.gather(*(tracker.wait_closed() for tracker in trackers))

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, context()])
def test_context_free_tool_retains_executor_signature(value):
    tool = definition(lambda *, relative_path: relative_path, required=False)
    assert tool.execute(Arguments(relative_path="x"), context=value) == "x"


@pytest.mark.parametrize("alias", [False, True])
def test_registration_rejects_reserved_field(alias):
    if alias:
        class Reserved(BaseModel):
            forged: str = Field(alias="context")
    else:
        class Reserved(BaseModel):
            context: str
    with pytest.raises(ValueError, match="context"):
        definition(lambda **kwargs: "unused", model=Reserved)


def test_extra_payload_cannot_override_context():
    class Permissive(BaseModel):
        model_config = ConfigDict(extra="allow")
        relative_path: str

    tool = definition(lambda **kwargs: pytest.fail("must not execute"), model=Permissive)
    arguments = tool.validate_arguments('{"relative_path":"x","context":{"user_id":9}}')
    with pytest.raises(ValueError, match="context"):
        tool.execute(arguments, context=context())


def test_wrong_argument_model_rejected():
    class Other(BaseModel):
        relative_path: str

    tool = definition(lambda **kwargs: pytest.fail("must not execute"))
    with pytest.raises(TypeError):
        tool.execute(Other(relative_path="x"), context=context())


def test_model_schema_has_no_server_context():
    schema = definition(lambda **kwargs: "unused").as_model_tool()["function"]["parameters"]
    assert set(schema["properties"]) == {"relative_path"}
    assert schema["additionalProperties"] is False


def test_model_context_injection_is_validation_error(monkeypatch):
    monkeypatch.setitem(TOOL_REGISTRY, "context_test", definition(lambda **kwargs: pytest.fail("must not execute")))

    async def injected(observations):
        if not observations:
            return runtime.ToolAction("call", "context_test", '{"relative_path":"x","context":{}}')
        return runtime.FinalAnswer("done")

    result = asyncio.run(runtime.run_agent_loop(injected, tool_context=context()))
    assert result.observations[0].code == "invalid_tool_arguments"
