import asyncio
import json
from datetime import datetime
from decimal import Decimal

import pytest
from openai import OpenAIError

from app.services import chat_service
from app.services.agent_runtime import (
    AgentLoopCompleted,
    AgentLoopResult,
    ModelUsage,
    ToolAction,
    ToolCallFailed,
    ToolCallStarted,
    ToolCallSucceeded,
    ToolErrorObservation,
    ToolObservation,
)
from app.services.model_pricing import (
    BEIJING_TIMEZONE,
    DeepSeekPricingSchedule,
    ModelPricing,
)
from openai.types.chat import ChatCompletionMessageParam


TEST_PEAK_PRICING = ModelPricing(
    cache_hit_input_cny_per_million=Decimal("0.10"),
    cache_miss_input_cny_per_million=Decimal("3.0"),
    output_cny_per_million=Decimal("9.0"),
)
TEST_OFF_PEAK_PRICING = ModelPricing(
    cache_hit_input_cny_per_million=Decimal("0.05"),
    cache_miss_input_cny_per_million=Decimal("1.5"),
    output_cny_per_million=Decimal("4.5"),
)
TEST_PRICING_SCHEDULE = DeepSeekPricingSchedule(
    peak=TEST_PEAK_PRICING,
    off_peak=TEST_OFF_PEAK_PRICING,
)
PEAK_PRICED_AT = datetime(
    2026,
    9,
    9,
    10,
    0,
    tzinfo=BEIJING_TIMEZONE,
)
OFF_PEAK_PRICED_AT = datetime(
    2026,
    9,
    9,
    20,
    0,
    tzinfo=BEIJING_TIMEZONE,
)


async def never_receive_cancellation(_: int) -> str:
    await asyncio.Future[None]()
    raise AssertionError("取消等待协程不应自行结束")


def decode_event(line: str) -> dict[str, object]:
    return json.loads(line)


def test_build_run_finished_payload_preserves_missing_usage(monkeypatch):
    monkeypatch.setattr(
        chat_service,
        "DEEPSEEK_PRICING_SCHEDULE",
        TEST_PRICING_SCHEDULE,
    )
    result = AgentLoopResult(
        status="completed",
        answer="直接回答",
        steps_taken=1,
        observations=(),
        model_usage=None,
        model_duration_ms=480,
        tool_duration_ms=0,
    )

    assert chat_service.build_run_finished_payload(
        result,
        priced_at=PEAK_PRICED_AT,
    ) == {
        "steps_taken": 1,
        "metrics": {
            "model_usage": None,
            "model_duration_ms": 480,
            "tool_duration_ms": 0,
            "estimated_cost_cny": None,
            "pricing": {
                "model": chat_service.settings.deepseek_model,
                "tier": "peak",
                "cache_hit_input_cny_per_million": "0.10",
                "cache_miss_input_cny_per_million": "3.0",
                "output_cny_per_million": "9.0",
            },
        },
    }


@pytest.mark.parametrize(
    ("priced_at", "expected_tier", "expected_cost_cny", "expected_pricing"),
    [
        (PEAK_PRICED_AT, "peak", "0.00044300", TEST_PEAK_PRICING),
        (
            OFF_PEAK_PRICED_AT,
            "off_peak",
            "0.00022150",
            TEST_OFF_PEAK_PRICING,
        ),
    ],
)
def test_build_run_finished_payload_uses_selected_cny_pricing(
    monkeypatch,
    priced_at,
    expected_tier,
    expected_cost_cny,
    expected_pricing,
):
    monkeypatch.setattr(
        chat_service,
        "DEEPSEEK_PRICING_SCHEDULE",
        TEST_PRICING_SCHEDULE,
    )
    result = AgentLoopResult(
        status="completed",
        answer="矩形面积是 12。",
        steps_taken=2,
        observations=(),
        model_usage=ModelUsage(
            input_tokens=120,
            output_tokens=35,
            total_tokens=155,
            cache_hit_input_tokens=80,
            cache_miss_input_tokens=40,
        ),
        model_duration_ms=1840,
        tool_duration_ms=12,
    )

    payload = chat_service.build_run_finished_payload(
        result,
        priced_at=priced_at,
    )
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["estimated_cost_cny"] == expected_cost_cny

    pricing = metrics["pricing"]
    assert isinstance(pricing, dict)
    assert pricing == {
        "model": chat_service.settings.deepseek_model,
        "tier": expected_tier,
        "cache_hit_input_cny_per_million": format(
            expected_pricing.cache_hit_input_cny_per_million,
            "f",
        ),
        "cache_miss_input_cny_per_million": format(
            expected_pricing.cache_miss_input_cny_per_million,
            "f",
        ),
        "output_cny_per_million": format(
            expected_pricing.output_cny_per_million,
            "f",
        ),
    }


def test_redis_cancellation_signal_aborts_stream(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "生成一段长回答"},
    ]
    cancellation_ready = asyncio.Event()
    stream_blocker = asyncio.Event()
    finished_runs: list[tuple[int, str, dict[str, object]]] = []

    async def fake_prepare_messages(session_id, prompt):
        return history, list(history)

    async def blocked_agent_loop(decide, *, max_steps):
        yield ToolCallStarted(
            action=ToolAction(
                tool_call_id="call-blocked",
                tool_name="get_current_time",
                arguments='{"utc_offset_hours": 8}',
            )
        )
        await stream_blocker.wait()

    async def fake_wait_for_cancellation(run_id: int) -> str:
        await cancellation_ready.wait()
        return "user"

    monkeypatch.setattr(
        chat_service,
        "_prepare_chat_messages",
        fake_prepare_messages,
    )
    monkeypatch.setattr(
        chat_service,
        "stream_agent_loop",
        blocked_agent_loop,
    )
    monkeypatch.setattr(chat_service, "record_run_event", lambda *args: None)
    monkeypatch.setattr(
        chat_service,
        "get_run_cancellation_reason",
        lambda run_id: "user",
    )
    monkeypatch.setattr(
        chat_service,
        "wait_for_run_cancellation",
        fake_wait_for_cancellation,
    )
    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status, payload=None: finished_runs.append(
            (run_id, status, payload or {}),
        ),
    )

    async def consume_until_cancelled():
        stream = chat_service.stream_chat_reply(
            session_id="redis-cancel-test",
            prompt="生成一段长回答",
            run_id=505,
        )

        first_event = decode_event(await anext(stream))
        assert first_event["type"] == "TOOL_CALL_START"
        await asyncio.sleep(0)
        cancellation_ready.set()

        try:
            await anext(stream)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Redis 取消通知没有中断 Agent 流")

    asyncio.run(consume_until_cancelled())

    assert history == [{"role": "system", "content": "system"}]
    assert finished_runs == [(505, "aborted", {"reason": "user"})]


def test_cancelled_stream_rolls_back_pending_user_message(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "生成一段长回答"},
    ]
    blocker = asyncio.Event()

    async def fake_prepare_messages(session_id, prompt):
        return history, list(history)

    async def blocked_agent_loop(decide, *, max_steps):
        yield ToolCallStarted(
            action=ToolAction(
                tool_call_id="call-cancelled",
                tool_name="get_current_time",
                arguments='{"utc_offset_hours": 8}',
            )
        )
        await blocker.wait()

    monkeypatch.setattr(
        chat_service,
        "_prepare_chat_messages",
        fake_prepare_messages,
    )
    monkeypatch.setattr(
        chat_service,
        "stream_agent_loop",
        blocked_agent_loop,
    )
    monkeypatch.setattr(
        chat_service,
        "get_run_cancellation_reason",
        lambda run_id: "user",
    )
    monkeypatch.setattr(
        chat_service,
        "wait_for_run_cancellation",
        never_receive_cancellation,
    )
    monkeypatch.setattr(chat_service, "record_run_event", lambda *args: None)
    finished_runs: list[tuple[int, str, dict[str, object]]] = []

    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status, payload=None: finished_runs.append(
            (run_id, status, payload or {}),
        ),
    )

    async def run_cancel():
        stream = chat_service.stream_chat_reply(
            session_id="cancel-test",
            prompt="生成一段长回答",
            run_id=101,
        )

        first_event = decode_event(await anext(stream))
        assert first_event["type"] == "TOOL_CALL_START"

        async def read_next_chunk():
            return await anext(stream)

        next_chunk = asyncio.create_task(read_next_chunk())
        await asyncio.sleep(0)
        next_chunk.cancel()

        try:
            await next_chunk
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("取消信号没有继续向上传播")

    asyncio.run(run_cancel())

    assert history == [{"role": "system", "content": "system"}]
    assert finished_runs == [(101, "aborted", {"reason": "user"})]


def test_completed_stream_records_chunks_and_finished_status(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "计算矩形面积"},
    ]
    observation = ToolObservation(
        tool_call_id="call-area",
        tool_name="calculate_rectangle_area",
        result="12",
        duration_ms=12,
    )
    recorded_events: list[tuple[int, str, dict[str, object]]] = []
    finished_runs: list[tuple[int, str, dict[str, object]]] = []
    saved_turns: list[dict[str, str]] = []
    loop_result = AgentLoopResult(
        status="completed",
        answer="矩形面积是 12。",
        steps_taken=2,
        observations=(observation,),
        model_usage=ModelUsage(
            input_tokens=120,
            output_tokens=35,
            total_tokens=155,
            cache_hit_input_tokens=80,
            cache_miss_input_tokens=40,
        ),
        model_duration_ms=1840,
        tool_duration_ms=12,
    )

    async def fake_prepare_messages(session_id, prompt):
        return history, list(history)

    async def fake_agent_loop(decide, *, max_steps):
        assert max_steps == 5
        yield ToolCallStarted(
            action=ToolAction(
                tool_call_id="call-area",
                tool_name="calculate_rectangle_area",
                arguments='{"width": 3, "height": 4}',
            )
        )
        yield ToolCallSucceeded(observation=observation)
        yield AgentLoopCompleted(result=loop_result)

    async def collect_stream():
        return [
            decode_event(line)
            async for line in chat_service.stream_chat_reply(
                session_id="agent-success",
                prompt="计算矩形面积",
                run_id=202,
            )
        ]

    monkeypatch.setattr(chat_service, "_prepare_chat_messages", fake_prepare_messages)
    monkeypatch.setattr(chat_service, "stream_agent_loop", fake_agent_loop)
    monkeypatch.setattr(
        chat_service,
        "DEEPSEEK_PRICING_SCHEDULE",
        TEST_PRICING_SCHEDULE,
    )
    original_payload_builder = chat_service.build_run_finished_payload
    monkeypatch.setattr(
        chat_service,
        "build_run_finished_payload",
        lambda result: original_payload_builder(
            result,
            priced_at=PEAK_PRICED_AT,
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "wait_for_run_cancellation",
        never_receive_cancellation,
    )
    monkeypatch.setattr(
        chat_service,
        "record_run_event",
        lambda run_id, event_type, payload: recorded_events.append(
            (run_id, event_type, payload)
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status, payload=None: finished_runs.append(
            (run_id, status, payload or {})
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "save_conversation_turn",
        lambda **kwargs: saved_turns.append(kwargs),
    )

    events = asyncio.run(collect_stream())

    assert [event["type"] for event in events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[0]["tool_call_id"] == "call-area"
    assert events[1]["result"] == "12"
    assert events[3]["chunk"] == "矩形面积是 12。"
    finished_payload = original_payload_builder(
        loop_result,
        priced_at=PEAK_PRICED_AT,
    )
    assert events[-1] == {
        "type": "RUN_FINISHED",
        **finished_payload,
    }

    assert [event_type for _, event_type, _ in recorded_events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    assert finished_runs == [(202, "done", finished_payload)]
    assert saved_turns == [
        {
            "session_id": "agent-success",
            "user_content": "计算矩形面积",
            "assistant_content": "矩形面积是 12。",
        }
    ]
    assert history[-1] == {
        "role": "assistant",
        "content": "矩形面积是 12。",
    }


def test_model_error_finishes_run_as_error_and_rolls_back_user_message(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "测试错误"},
    ]
    finished_runs: list[tuple[int, str, dict[str, object]]] = []

    async def fake_prepare_messages(session_id, prompt):
        return history, list(history)

    async def failing_agent_loop(decide, *, max_steps):
        raise OpenAIError("不应暴露的模型错误")
        yield

    async def collect_stream():
        return [
            decode_event(line)
            async for line in chat_service.stream_chat_reply(
                session_id="agent-model-error",
                prompt="测试错误",
                run_id=303,
            )
        ]

    monkeypatch.setattr(chat_service, "_prepare_chat_messages", fake_prepare_messages)
    monkeypatch.setattr(chat_service, "stream_agent_loop", failing_agent_loop)
    monkeypatch.setattr(
        chat_service,
        "wait_for_run_cancellation",
        never_receive_cancellation,
    )
    monkeypatch.setattr(chat_service, "record_run_event", lambda *args: None)
    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status, payload=None: finished_runs.append(
            (run_id, status, payload or {})
        ),
    )

    events = asyncio.run(collect_stream())

    assert events == [
        {
            "type": "RUN_ERROR",
            "code": "model_unavailable",
            "message": "模型服务暂时不可用",
        }
    ]
    assert "不应暴露" not in str(events)
    assert history == [{"role": "system", "content": "system"}]
    assert finished_runs == [
        (
            303,
            "error",
            {
                "code": "model_unavailable",
                "message": "模型服务暂时不可用",
            },
        )
    ]


def test_timed_out_stream_finishes_as_error(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "生成一段长回答"},
    ]
    stream_blocker = asyncio.Event()
    finished_runs: list[tuple[int, str, dict[str, object]]] = []

    async def fake_prepare_messages(session_id, prompt):
        return history, list(history)

    async def blocked_agent_loop(decide, *, max_steps):
        yield ToolCallStarted(
            action=ToolAction(
                tool_call_id="call-timeout",
                tool_name="get_current_time",
                arguments='{"utc_offset_hours": 8}',
            )
        )
        await stream_blocker.wait()

    monkeypatch.setattr(
        chat_service,
        "_prepare_chat_messages",
        fake_prepare_messages,
    )
    monkeypatch.setattr(
        chat_service,
        "stream_agent_loop",
        blocked_agent_loop,
    )
    monkeypatch.setattr(chat_service, "record_run_event", lambda *args: None)
    monkeypatch.setattr(
        chat_service,
        "get_run_cancellation_reason",
        lambda run_id: "timeout",
    )
    monkeypatch.setattr(
        chat_service,
        "wait_for_run_cancellation",
        never_receive_cancellation,
    )
    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status, payload=None: finished_runs.append(
            (run_id, status, payload or {}),
        ),
    )

    async def cancel_stream():
        stream = chat_service.stream_chat_reply(
            session_id="timeout-test",
            prompt="生成一段长回答",
            run_id=404,
        )

        first_event = decode_event(await anext(stream))
        assert first_event["type"] == "TOOL_CALL_START"

        async def read_next_chunk() -> str:
            return await anext(stream)

        next_chunk = asyncio.create_task(read_next_chunk())
        await asyncio.sleep(0)
        next_chunk.cancel()

        try:
            await next_chunk
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("取消信号没有继续向上传播")

    asyncio.run(cancel_stream())

    assert history == [{"role": "system", "content": "system"}]
    assert finished_runs == [(404, "error", {"reason": "timeout"})]


def test_tool_error_is_emitted_and_model_can_still_finish(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "调用未知工具"},
    ]
    error_observation = ToolErrorObservation(
        tool_call_id="call-unknown",
        tool_name="read_secret_file",
        code="unknown_tool",
        message="工具未注册：read_secret_file",
    )

    async def fake_prepare_messages(session_id, prompt):
        return history, list(history)

    async def fake_agent_loop(decide, *, max_steps):
        yield ToolCallStarted(
            action=ToolAction(
                tool_call_id="call-unknown",
                tool_name="read_secret_file",
                arguments="{}",
            )
        )
        yield ToolCallFailed(observation=error_observation)
        yield AgentLoopCompleted(
            result=AgentLoopResult(
                status="completed",
                answer="该工具不可用。",
                steps_taken=2,
                observations=(error_observation,),
            )
        )

    monkeypatch.setattr(chat_service, "_prepare_chat_messages", fake_prepare_messages)
    monkeypatch.setattr(chat_service, "stream_agent_loop", fake_agent_loop)
    monkeypatch.setattr(
        chat_service,
        "wait_for_run_cancellation",
        never_receive_cancellation,
    )
    monkeypatch.setattr(chat_service, "record_run_event", lambda *args: None)
    monkeypatch.setattr(chat_service, "finish_agent_run", lambda *args: None)
    monkeypatch.setattr(chat_service, "save_conversation_turn", lambda **kwargs: None)

    async def collect_stream():
        return [
            decode_event(line)
            async for line in chat_service.stream_chat_reply(
                session_id="agent-tool-error",
                prompt="调用未知工具",
                run_id=606,
            )
        ]

    events = asyncio.run(collect_stream())

    assert [event["type"] for event in events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ERROR",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[1]["code"] == "unknown_tool"
    assert events[3]["chunk"] == "该工具不可用。"


def test_max_steps_emits_run_error_and_rolls_back_turn(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "一直调用工具"},
    ]
    finished_runs: list[tuple[int, str, dict[str, object]]] = []

    async def fake_prepare_messages(session_id, prompt):
        return history, list(history)

    async def fake_agent_loop(decide, *, max_steps):
        yield AgentLoopCompleted(
            result=AgentLoopResult(
                status="max_steps_exceeded",
                answer=None,
                steps_taken=5,
                observations=(),
            )
        )

    monkeypatch.setattr(chat_service, "_prepare_chat_messages", fake_prepare_messages)
    monkeypatch.setattr(chat_service, "stream_agent_loop", fake_agent_loop)
    monkeypatch.setattr(
        chat_service,
        "wait_for_run_cancellation",
        never_receive_cancellation,
    )
    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status, payload=None: finished_runs.append(
            (run_id, status, payload or {})
        ),
    )

    async def collect_stream():
        return [
            decode_event(line)
            async for line in chat_service.stream_chat_reply(
                session_id="agent-max-steps",
                prompt="一直调用工具",
                run_id=707,
            )
        ]

    events = asyncio.run(collect_stream())

    assert events == [
        {
            "type": "RUN_ERROR",
            "code": "max_steps_exceeded",
            "message": "Agent 达到最大执行步数",
        }
    ]
    assert history == [{"role": "system", "content": "system"}]
    assert finished_runs == [
        (
            707,
            "error",
            {
                "code": "max_steps_exceeded",
                "message": "Agent 达到最大执行步数",
            },
        )
    ]


def test_rollback_pending_turn_removes_user_and_partial_assistant():
    history: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "本轮问题"},
        {"role": "assistant", "content": "尚未提交的回答"},
    ]

    chat_service.rollback_pending_turn(history, 1)

    assert history == [{"role": "system", "content": "system"}]
