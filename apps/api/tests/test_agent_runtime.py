import asyncio
from threading import Event
from time import sleep

import pytest

from app.services.agent_runtime import (
    AgentLoopResult,
    AgentObservation,
    FinalAnswer,
    ToolAction,
    ToolErrorObservation,
    ToolObservation,
    run_agent_loop,
)
from app.tools.registry import (
    TOOL_REGISTRY,
    GetCurrentTimeArguments,
    ToolDefinition,
)


def test_agent_loop_executes_tool_and_returns_final_answer():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
            )

        last_observation = observations[-1]
        assert isinstance(last_observation, ToolObservation)

        return FinalAnswer(content=f"矩形面积是 {last_observation.result}")

    result = asyncio.run(
        run_agent_loop(
            decide,
            max_steps=3,
        )
    )

    assert result == AgentLoopResult(
        status="completed",
        answer="矩形面积是 12",
        steps_taken=2,
        observations=(
            ToolObservation(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                result="12",
            ),
        ),
    )


def test_agent_loop_stops_at_max_steps():
    async def always_request_tool(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction:
        return ToolAction(
            tool_call_id=f"call-{len(observations) + 1}",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
        )

    result = asyncio.run(
        run_agent_loop(
            always_request_tool,
            max_steps=2,
        )
    )

    assert result.status == "max_steps_exceeded"
    assert result.answer is None
    assert result.steps_taken == 2
    assert len(result.observations) == 2


def test_agent_loop_returns_unknown_tool_as_observation():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-unknown",
                tool_name="read_secret_file",
                arguments="{}",
            )

        error_observation = observations[-1]
        assert isinstance(error_observation, ToolErrorObservation)

        return FinalAnswer(
            content=f"工具调用失败：{error_observation.code}",
        )

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert result.status == "completed"
    assert result.answer == "工具调用失败：unknown_tool"
    assert result.steps_taken == 2
    assert result.observations == (
        ToolErrorObservation(
            tool_call_id="call-unknown",
            tool_name="read_secret_file",
            code="unknown_tool",
            message="工具未注册：read_secret_file",
        ),
    )


def test_agent_loop_can_retry_after_invalid_arguments():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-invalid",
                tool_name="calculate_rectangle_area",
                arguments='{"width": -3, "height": 4}',
            )

        if len(observations) == 1:
            error_observation = observations[0]
            assert isinstance(error_observation, ToolErrorObservation)
            assert error_observation.code == "invalid_tool_arguments"

            return ToolAction(
                tool_call_id="call-retry",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
            )

        success_observation = observations[-1]
        assert isinstance(success_observation, ToolObservation)

        return FinalAnswer(
            content=f"矩形面积是 {success_observation.result}",
        )

    result = asyncio.run(run_agent_loop(decide, max_steps=4))

    assert result.status == "completed"
    assert result.answer == "矩形面积是 12"
    assert result.steps_taken == 3
    assert len(result.observations) == 2

    first_observation = result.observations[0]
    second_observation = result.observations[1]

    assert isinstance(first_observation, ToolErrorObservation)
    assert first_observation.code == "invalid_tool_arguments"
    assert first_observation.details is not None

    assert second_observation == ToolObservation(
        tool_call_id="call-retry",
        tool_name="calculate_rectangle_area",
        result="12",
    )


def test_agent_loop_rejects_invalid_max_steps():
    async def decide(
        _: tuple[AgentObservation, ...],
    ) -> FinalAnswer:
        return FinalAnswer(content="不会执行")

    with pytest.raises(ValueError, match="max_steps 必须大于等于 1"):
        asyncio.run(run_agent_loop(decide, max_steps=0))


def test_agent_loop_returns_executor_failure_as_observation(monkeypatch):
    def failing_executor(*, utc_offset_hours: int) -> str:
        raise RuntimeError("不应暴露的内部错误")

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "failing_tool",
        ToolDefinition(
            name="failing_tool",
            description="测试执行失败",
            arguments_model=GetCurrentTimeArguments,
            executor=failing_executor,
        ),
    )

    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-failed",
                tool_name="failing_tool",
                arguments='{"utc_offset_hours": 8}',
            )

        error_observation = observations[-1]
        assert isinstance(error_observation, ToolErrorObservation)
        return FinalAnswer(content="工具失败，已停止")

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert result.status == "completed"
    assert result.answer == "工具失败，已停止"

    error_observation = result.observations[0]
    assert isinstance(error_observation, ToolErrorObservation)
    assert error_observation.code == "tool_execution_failed"
    assert error_observation.message == "工具执行失败"
    assert error_observation.details == "RuntimeError"
    assert "不应暴露的内部错误" not in str(error_observation)


def test_agent_loop_returns_timeout_as_observation(monkeypatch):
    def slow_executor(*, utc_offset_hours: int) -> str:
        sleep(0.05)
        return "不会及时返回"

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "slow_tool",
        ToolDefinition(
            name="slow_tool",
            description="测试执行超时",
            arguments_model=GetCurrentTimeArguments,
            executor=slow_executor,
            timeout_seconds=0.001,
        ),
    )

    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-timeout",
                tool_name="slow_tool",
                arguments='{"utc_offset_hours": 8}',
            )

        error_observation = observations[-1]
        assert isinstance(error_observation, ToolErrorObservation)
        return FinalAnswer(content="工具超时，已降级")

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert result.status == "completed"
    assert result.answer == "工具超时，已降级"

    error_observation = result.observations[0]
    assert isinstance(error_observation, ToolErrorObservation)
    assert error_observation.code == "tool_timeout"
    assert error_observation.message == "工具执行超时"
    assert error_observation.details == "timeout_seconds=0.001"


def test_tool_definition_rejects_non_positive_timeout():
    with pytest.raises(ValueError, match="timeout_seconds 必须大于 0"):
        ToolDefinition(
            name="invalid_timeout_tool",
            description="测试非法超时配置",
            arguments_model=GetCurrentTimeArguments,
            executor=lambda **_: "不会执行",
            timeout_seconds=0,
        )


def test_agent_loop_propagates_cancellation_during_tool_execution(monkeypatch):
    executor_started = Event()

    def slow_executor(*, utc_offset_hours: int) -> str:
        executor_started.set()
        sleep(0.05)
        return "不会被使用"

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "cancellable_tool",
        ToolDefinition(
            name="cancellable_tool",
            description="测试取消传播",
            arguments_model=GetCurrentTimeArguments,
            executor=slow_executor,
            timeout_seconds=1,
        ),
    )

    async def decide(
        _: tuple[AgentObservation, ...],
    ) -> ToolAction:
        return ToolAction(
            tool_call_id="call-cancelled",
            tool_name="cancellable_tool",
            arguments='{"utc_offset_hours": 8}',
        )

    async def cancel_running_loop() -> None:
        loop_task = asyncio.create_task(run_agent_loop(decide, max_steps=2))

        while not executor_started.is_set():
            await asyncio.sleep(0)

        loop_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await loop_task

    asyncio.run(cancel_running_loop())
