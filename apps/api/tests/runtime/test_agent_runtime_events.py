import asyncio

import pytest

from app.services.runtime.agent_runtime import (
    AgentLoopCompleted,
    AgentLoopEvent,
    AgentObservation,
    FinalAnswer,
    ModelUsage,
    ToolAction,
    ToolCallFailed,
    ToolCallStarted,
    ToolCallSucceeded,
    ToolErrorObservation,
    ToolObservation,
    stream_agent_loop,
)


async def collect_events(
    decide,
    *,
    max_steps: int = 5,
    max_total_tokens: int | None = None,
) -> list[AgentLoopEvent]:
    return [
        event
        async for event in stream_agent_loop(
            decide,
            max_steps=max_steps,
            max_total_tokens=max_total_tokens,
        )
    ]


def test_stream_agent_loop_emits_successful_tool_sequence():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
            )

        return FinalAnswer(content="矩形面积是 12")

    events = asyncio.run(collect_events(decide))

    assert [type(event) for event in events] == [
        ToolCallStarted,
        ToolCallSucceeded,
        AgentLoopCompleted,
    ]

    started = events[0]
    assert isinstance(started, ToolCallStarted)
    assert started.action.tool_call_id == "call-area"

    succeeded = events[1]
    assert isinstance(succeeded, ToolCallSucceeded)
    assert succeeded.observation == ToolObservation(
        tool_call_id="call-area",
        tool_name="calculate_rectangle_area",
        result="12",
    )

    completed = events[2]
    assert isinstance(completed, AgentLoopCompleted)
    assert completed.result.status == "completed"
    assert completed.result.answer == "矩形面积是 12"
    assert completed.result.steps_taken == 2


def test_stream_agent_loop_emits_failure_before_model_recovers():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-unknown",
                tool_name="read_secret_file",
                arguments="{}",
            )

        error = observations[-1]
        assert isinstance(error, ToolErrorObservation)
        return FinalAnswer(content="该工具不可用")

    events = asyncio.run(collect_events(decide))

    assert [type(event) for event in events] == [
        ToolCallStarted,
        ToolCallFailed,
        AgentLoopCompleted,
    ]

    failed = events[1]
    assert isinstance(failed, ToolCallFailed)
    assert failed.observation.code == "unknown_tool"
    assert failed.observation.tool_call_id == "call-unknown"

    completed = events[2]
    assert isinstance(completed, AgentLoopCompleted)
    assert completed.result.answer == "该工具不可用"
    assert completed.result.observations == (failed.observation,)


def test_stream_agent_loop_emits_one_terminal_event_at_max_steps():
    async def always_request_tool(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction:
        return ToolAction(
            tool_call_id=f"call-{len(observations) + 1}",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
        )

    events = asyncio.run(
        collect_events(
            always_request_tool,
            max_steps=2,
        )
    )

    assert [type(event) for event in events] == [
        ToolCallStarted,
        ToolCallSucceeded,
        ToolCallStarted,
        ToolCallSucceeded,
        AgentLoopCompleted,
    ]

    terminal_events = [
        event for event in events if isinstance(event, AgentLoopCompleted)
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0].result.status == "max_steps_exceeded"
    assert terminal_events[0].result.steps_taken == 2
    assert len(terminal_events[0].result.observations) == 2


def test_stream_agent_loop_continues_while_token_budget_remains():
    async def decide(
        observations: tuple[AgentObservation, ...],
    ) -> ToolAction | FinalAnswer:
        if not observations:
            return ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
                model_usage=ModelUsage(
                    input_tokens=7,
                    output_tokens=2,
                    total_tokens=9,
                ),
            )

        return FinalAnswer(
            content="矩形面积是 12",
            model_usage=ModelUsage(
                input_tokens=0,
                output_tokens=1,
                total_tokens=1,
            ),
        )

    events = asyncio.run(
        collect_events(
            decide,
            max_total_tokens=10,
        )
    )

    assert [type(event) for event in events] == [
        ToolCallStarted,
        ToolCallSucceeded,
        AgentLoopCompleted,
    ]
    completed = events[-1]
    assert isinstance(completed, AgentLoopCompleted)
    assert completed.result.status == "completed"
    assert completed.result.answer == "矩形面积是 12"
    assert completed.result.model_usage is not None
    assert completed.result.model_usage.total_tokens == 10


@pytest.mark.parametrize("total_tokens", [10, 11])
def test_stream_agent_loop_stops_before_tool_when_budget_is_exhausted(
    total_tokens: int,
):
    async def request_tool(
        _: tuple[AgentObservation, ...],
    ) -> ToolAction:
        return ToolAction(
            tool_call_id="call-area",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
            model_usage=ModelUsage(
                input_tokens=total_tokens,
                output_tokens=0,
                total_tokens=total_tokens,
            ),
        )

    events = asyncio.run(
        collect_events(
            request_tool,
            max_total_tokens=10,
        )
    )

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, AgentLoopCompleted)
    assert completed.result.status == "token_budget_exhausted"
    assert completed.result.answer is None
    assert completed.result.steps_taken == 1
    assert completed.result.observations == ()
    assert completed.result.model_usage is not None
    assert completed.result.model_usage.total_tokens == total_tokens


def test_stream_agent_loop_fails_closed_before_tool_when_usage_is_unknown():
    async def request_tool(
        _: tuple[AgentObservation, ...],
    ) -> ToolAction:
        return ToolAction(
            tool_call_id="call-area",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
        )

    events = asyncio.run(
        collect_events(
            request_tool,
            max_total_tokens=10,
        )
    )

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, AgentLoopCompleted)
    assert completed.result.status == "token_usage_unknown"
    assert completed.result.answer is None
    assert completed.result.observations == ()
    assert completed.result.model_usage is None


def test_stream_agent_loop_delivers_final_answer_at_token_limit():
    async def answer(
        _: tuple[AgentObservation, ...],
    ) -> FinalAnswer:
        return FinalAnswer(
            content="已经生成的最终答案",
            model_usage=ModelUsage(
                input_tokens=8,
                output_tokens=2,
                total_tokens=10,
            ),
        )

    events = asyncio.run(
        collect_events(
            answer,
            max_total_tokens=10,
        )
    )

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, AgentLoopCompleted)
    assert completed.result.status == "completed"
    assert completed.result.answer == "已经生成的最终答案"
    assert completed.result.model_usage is not None
    assert completed.result.model_usage.total_tokens == 10
