import asyncio
from threading import Event
from time import sleep

import pytest

import app.services.runtime.agent.agent_runtime as agent_runtime_module
from app.services.runtime.agent.agent_runtime import (
    AgentLoopResult,
    AgentObservation,
    FinalAnswer,
    ModelUsage,
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


def test_run_agent_loop_forwards_token_budget():
    async def request_tool(
        _: tuple[AgentObservation, ...],
    ) -> ToolAction:
        return ToolAction(
            tool_call_id="call-area",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
            model_usage=ModelUsage(
                input_tokens=8,
                output_tokens=2,
                total_tokens=10,
            ),
        )

    result = asyncio.run(
        run_agent_loop(
            request_tool,
            max_total_tokens=10,
        )
    )

    assert result.status == "token_budget_exhausted"
    assert result.steps_taken == 1
    assert result.observations == ()


def test_agent_loop_accumulates_model_usage_across_steps():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
                model_usage=ModelUsage(
                    input_tokens=20,
                    output_tokens=4,
                    total_tokens=24,
                    cache_hit_input_tokens=8,
                    cache_miss_input_tokens=12,
                ),
            )

        return FinalAnswer(
            content="矩形面积是 12",
            model_usage=ModelUsage(
                input_tokens=30,
                output_tokens=6,
                total_tokens=36,
                cache_hit_input_tokens=20,
                cache_miss_input_tokens=10,
            ),
        )

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert result.model_usage == ModelUsage(
        input_tokens=50,
        output_tokens=10,
        total_tokens=60,
        cache_hit_input_tokens=28,
        cache_miss_input_tokens=22,
    )


def test_agent_loop_hides_partial_usage_when_any_step_is_missing():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
                model_usage=ModelUsage(
                    input_tokens=20,
                    output_tokens=4,
                    total_tokens=24,
                    cache_hit_input_tokens=8,
                    cache_miss_input_tokens=12,
                ),
            )

        return FinalAnswer(content="矩形面积是 12")

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert result.model_usage is None


def test_agent_loop_accumulates_model_duration_across_steps():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
                model_duration_ms=120,
            )

        return FinalAnswer(
            content="矩形面积是 12",
            model_duration_ms=80,
        )

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert result.model_duration_ms == 200


def test_agent_loop_hides_partial_duration_when_any_step_is_missing():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
                model_duration_ms=120,
            )

        return FinalAnswer(content="矩形面积是 12")

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert result.model_duration_ms is None


def test_agent_loop_records_and_accumulates_tool_duration(monkeypatch):
    clock_values = iter(
        [
            1_000_000_000,
            1_010_900_000,
            2_000_000_000,
            2_020_400_000,
        ]
    )
    monkeypatch.setattr(
        agent_runtime_module,
        "perf_counter_ns",
        lambda: next(clock_values),
    )

    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if len(observations) < 2:
            call_number = len(observations) + 1
            return ToolAction(
                tool_call_id=f"call-area-{call_number}",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
            )

        return FinalAnswer(content="两个矩形的面积都是 12")

    result = asyncio.run(run_agent_loop(decide, max_steps=3))

    assert [observation.duration_ms for observation in result.observations] == [
        10,
        20,
    ]
    assert result.tool_duration_ms == 30


def test_agent_loop_does_not_time_argument_validation(monkeypatch):
    def fail_if_clock_is_called() -> int:
        raise AssertionError("参数校验失败时不应开始工具执行计时")

    monkeypatch.setattr(
        agent_runtime_module,
        "perf_counter_ns",
        fail_if_clock_is_called,
    )

    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-invalid",
                tool_name="calculate_rectangle_area",
                arguments='{"width": -3, "height": 4}',
            )

        return FinalAnswer(content="参数错误")

    result = asyncio.run(run_agent_loop(decide, max_steps=2))

    error_observation = result.observations[0]
    assert isinstance(error_observation, ToolErrorObservation)
    assert error_observation.duration_ms is None
    assert result.tool_duration_ms == 0


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
    clock_values = iter([1_000_000_000, 1_007_800_000])
    monkeypatch.setattr(
        agent_runtime_module,
        "perf_counter_ns",
        lambda: next(clock_values),
    )

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
    assert error_observation.duration_ms == 7
    assert result.tool_duration_ms == 7
    assert "不应暴露的内部错误" not in str(error_observation)


def test_agent_loop_returns_timeout_as_observation(monkeypatch):
    clock_values = iter([1_000_000_000, 1_011_900_000])
    monkeypatch.setattr(
        agent_runtime_module,
        "perf_counter_ns",
        lambda: next(clock_values),
    )

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
    assert error_observation.duration_ms == 11
    assert result.tool_duration_ms == 11


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
