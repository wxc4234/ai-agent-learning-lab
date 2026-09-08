import asyncio

from app.services.agent_runtime import (
    AgentLoopCompleted,
    AgentLoopEvent,
    AgentObservation,
    FinalAnswer,
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
) -> list[AgentLoopEvent]:
    return [
        event
        async for event in stream_agent_loop(
            decide,
            max_steps=max_steps,
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
