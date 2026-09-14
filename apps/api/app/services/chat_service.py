import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime, timezone

from openai import OpenAIError
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings
from app.repositories.conversation_repository import (
    ensure_owned_conversation,
    load_conversation,
    save_conversation_turn,
)
from app.repositories.run_repository import (
    finish_agent_run,
    get_run_cancellation_reason,
    record_run_event,
)
from app.services.agent_runtime import (
    AgentLoopCompleted,
    AgentLoopResult,
    ToolCallFailed,
    ToolCallStarted,
    ToolCallSucceeded,
    stream_agent_loop,
)
from app.services.model_client import client
from app.services.model_decision import (
    DEFAULT_SYSTEM_PROMPT,
    DeepSeekDecisionMaker,
    ModelDecisionError,
)
from app.services.model_pricing import (
    DeepSeekPricingSchedule,
    ModelPricing,
    estimate_model_cost_cny,
)
from app.services.run_cancellation import wait_for_run_cancellation
from app.services.tool_event_payloads import (
    build_tool_call_error_payload,
    build_tool_call_result_payload,
)

# 用户身份参与缓存定位；访问缓存前仍检查数据库中的会话归属。
conversations: dict[
    tuple[int, str],
    list[ChatCompletionMessageParam],
] = {}

# 只发送最近 5 轮，控制上下文长度、延迟和模型调用成本。
MAX_ROUNDS = 5

# Runtime 只返回稳定状态码，聊天服务负责决定用户可见消息。
AGENT_LOOP_ERROR_MESSAGES: dict[str, str] = {
    "max_steps_exceeded": "Agent 达到最大执行步数",
    "token_budget_exhausted": "本次运行已达到 Token 预算上限",
    "token_usage_unknown": "无法确认本次运行的 Token 用量，已停止继续执行",
}

# 按北京时间区分高峰/低谷时段，用于估算模型调用费用。
DEEPSEEK_PRICING_SCHEDULE = DeepSeekPricingSchedule(
    peak=ModelPricing(
        cache_hit_input_cny_per_million=(
            settings.deepseek_peak_cache_hit_input_cny_per_million
        ),
        cache_miss_input_cny_per_million=(
            settings.deepseek_peak_cache_miss_input_cny_per_million
        ),
        output_cny_per_million=(settings.deepseek_peak_output_cny_per_million),
    ),
    off_peak=ModelPricing(
        cache_hit_input_cny_per_million=(
            settings.deepseek_off_peak_cache_hit_input_cny_per_million
        ),
        cache_miss_input_cny_per_million=(
            settings.deepseek_off_peak_cache_miss_input_cny_per_million
        ),
        output_cny_per_million=(settings.deepseek_off_peak_output_cny_per_million),
    ),
)


def encode_stream_event(
    event_type: str, payload: dict[str, object] | None = None
) -> str:
    """将一个 Agent 事件编码为一行完整 JSON。"""
    event: dict[str, object] = {"type": event_type}

    if payload is not None:
        event.update(payload)

    return json.dumps(event, ensure_ascii=False) + "\n"


# 构造稳定的 RUN_FINISHED 公共事件协议
def build_run_metrics_payload(
    result: AgentLoopResult,
    *,
    priced_at: datetime | None = None,
) -> dict[str, object]:
    """把 Runtime 已产生的指标转换为公共事件 Payload。"""

    if priced_at is None:
        priced_at = datetime.now(timezone.utc)

    pricing_tier, pricing = DEEPSEEK_PRICING_SCHEDULE.select(priced_at)

    estimated_cost_cny = estimate_model_cost_cny(
        result.model_usage,
        pricing,
    )

    model_usage_payload: dict[str, int | None] | None = None

    if result.model_usage is not None:
        model_usage_payload = {
            "input_tokens": result.model_usage.input_tokens,
            "output_tokens": result.model_usage.output_tokens,
            "total_tokens": result.model_usage.total_tokens,
            "cache_hit_input_tokens": (
                result.model_usage.cache_hit_input_tokens
            ),
            "cache_miss_input_tokens": (
                result.model_usage.cache_miss_input_tokens
            ),
        }

    # 保存当次运行采用的价格快照，历史费用才能被复核。
    pricing_payload: dict[str, str] = {
        "model": settings.deepseek_model,
        "tier": pricing_tier,
        "cache_hit_input_cny_per_million": format(
            pricing.cache_hit_input_cny_per_million,
            "f",
        ),
        "cache_miss_input_cny_per_million": format(
            pricing.cache_miss_input_cny_per_million,
            "f",
        ),
        "output_cny_per_million": format(
            pricing.output_cny_per_million,
            "f",
        ),
    }

    return {
        "model_usage": model_usage_payload,
        "model_duration_ms": result.model_duration_ms,
        "tool_duration_ms": result.tool_duration_ms,

        # JSON 不支持 Decimal，继续用字符串保存精确金额。
        "estimated_cost_cny": (
            format(estimated_cost_cny, "f")
            if estimated_cost_cny is not None
            else None
        ),
        "pricing": pricing_payload,
    }


def build_run_finished_payload(
    result: AgentLoopResult,
    *,
    priced_at: datetime | None = None,
) -> dict[str, object]:
    """构造正常完成的 RUN_FINISHED Payload。"""

    return {
        "steps_taken": result.steps_taken,

        # 正常完成也复用公共指标构造函数。
        "metrics": build_run_metrics_payload(
            result,
            priced_at=priced_at,
        ),
    }


def rollback_pending_turn(
    history: list[ChatCompletionMessageParam] | None, history_checkpoint: int | None
) -> None:
    """删除本轮开始后追加的 user 和可能存在的 assistant 消息。"""
    if history is None or history_checkpoint is None:
        return

    del history[history_checkpoint:]


async def _cancel_stream_when_requested(
    run_id: int,
    stream_task: asyncio.Task[object],
) -> None:
    """收到跨实例取消通知后，中断承载流的协程任务。"""

    await wait_for_run_cancellation(run_id)
    stream_task.cancel()


async def _prepare_chat_messages(
    *,
    user_id: int,
    session_id: str,
    prompt: str,
) -> tuple[list[ChatCompletionMessageParam], list[ChatCompletionMessageParam]]:
    await asyncio.to_thread(
        ensure_owned_conversation,
        user_id=user_id,
        session_id=session_id,
    )

    cache_key = (user_id, session_id)

    if cache_key not in conversations:
        saved_history = await asyncio.to_thread(
            load_conversation,
            user_id=user_id,
            session_id=session_id,
        )

        loaded_history: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": DEFAULT_SYSTEM_PROMPT,
            },
        ]

        for message in saved_history:
            if message["role"] == "user":
                loaded_history.append(
                    {
                        "role": "user",
                        "content": message["content"],
                    },
                )
            elif message["role"] == "assistant":
                loaded_history.append(
                    {
                        "role": "assistant",
                        "content": message["content"],
                    },
                )

        conversations[cache_key] = loaded_history

    history = conversations[cache_key]
    history.append({"role": "user", "content": prompt})

    recent_history = history[1:-1][-(MAX_ROUNDS * 2):]
    messages_to_send: list[ChatCompletionMessageParam] = [
        history[0],
        *recent_history,
        history[-1],
    ]

    return history, messages_to_send

async def create_chat_reply(
    *,
    user_id: int,
    session_id: str,
    prompt: str,
) -> str:
    """在当前用户拥有的会话中完成一轮普通聊天。"""
    history, messages_to_send = await _prepare_chat_messages(
        user_id=user_id,
        session_id=session_id,
        prompt=prompt,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages_to_send,
            stream=False,
        )
        reply = response.choices[0].message.content or ""

        await asyncio.to_thread(
            save_conversation_turn,
            user_id=user_id,
            session_id=session_id,
            user_content=prompt,
            assistant_content=reply,
        )

        history.append(
            {
                "role": "assistant",
                "content": reply,
            },
        )

        max_saved_messages = MAX_ROUNDS * 2
        if len(history) > max_saved_messages + 1:
            del history[1:-max_saved_messages]

        return reply
    except OpenAIError:
        history.pop()
        raise

# ===== 本课修改：正式聊天改为 Agent Runtime 结构化事件流 =====


async def stream_chat_reply(
    *,
    user_id: int,
    session_id: str,
    prompt: str,
    run_id: int,
) -> AsyncIterator[str]:
    """运行 Agent Loop，并逐行返回结构化 NDJSON 事件。"""

    history: list[ChatCompletionMessageParam] | None = None
    history_checkpoint: int | None = None
    turn_persisted = False

    stream_task = asyncio.current_task()
    if stream_task is None:
        raise RuntimeError("流式回复必须在 asyncio Task 中执行")

    cancellation_monitor = asyncio.create_task(
        _cancel_stream_when_requested(run_id, stream_task)
    )

    try:
        history, messages_to_send = await _prepare_chat_messages(
            user_id=user_id,
            session_id=session_id,
            prompt=prompt,
        )

        # _prepare_chat_messages 已把当前 user 追加到 history。
        # 因此最后一个元素的位置就是本轮开始点
        history_checkpoint = len(history) - 1

        decision_maker = DeepSeekDecisionMaker(
            client=client,
            model=settings.deepseek_model,
            messages=messages_to_send,
        )

        async for event in stream_agent_loop(
            decision_maker,
            max_steps=5,
            max_total_tokens=settings.agent_max_total_tokens,
        ):
            if isinstance(event, ToolCallStarted):
                payload: dict[str, object] = {
                    "tool_call_id": event.action.tool_call_id,
                    "tool_name": event.action.tool_name,
                    "arguments": event.action.arguments,
                }

                await asyncio.to_thread(
                    record_run_event,
                    run_id,
                    "TOOL_CALL_START",
                    payload,
                )

                yield encode_stream_event(
                    "TOOL_CALL_START",
                    payload,
                )
                continue

            if isinstance(event, ToolCallSucceeded):
                payload = build_tool_call_result_payload(event.observation)

                await asyncio.to_thread(
                    record_run_event,
                    run_id,
                    "TOOL_CALL_RESULT",
                    payload,
                )

                yield encode_stream_event(
                    "TOOL_CALL_RESULT",
                    payload,
                )
                continue

            if isinstance(event, ToolCallFailed):
                payload = build_tool_call_error_payload(event.observation)

                await asyncio.to_thread(
                    record_run_event,
                    run_id,
                    "TOOL_CALL_ERROR",
                    payload,
                )

                yield encode_stream_event(
                    "TOOL_CALL_ERROR",
                    payload,
                )
                continue

            if isinstance(event, AgentLoopCompleted):
                result = event.result

                if result.status != "completed":
                    rollback_pending_turn(
                        history,
                        history_checkpoint,
                    )
                    history = None

                    error_payload: dict[str, object] = {
                        "code": result.status,
                        "message": AGENT_LOOP_ERROR_MESSAGES[result.status],
                        "steps_taken": result.steps_taken,
                        "metrics": build_run_metrics_payload(result),
                    }

                    await asyncio.to_thread(
                        finish_agent_run,
                        run_id,
                        "error",
                        error_payload,
                    )

                    yield encode_stream_event(
                        "RUN_ERROR",
                        error_payload,
                    )
                    return

                if result.answer is None:
                    raise RuntimeError("已完成的 Agent Loop 没有最终答案")

                reply = result.answer

                await asyncio.to_thread(
                    record_run_event,
                    run_id,
                    "TEXT_MESSAGE_START",
                    {},
                )
                yield encode_stream_event("TEXT_MESSAGE_START")

                await asyncio.to_thread(
                    record_run_event,
                    run_id,
                    "TEXT_MESSAGE_CONTENT",
                    {
                        "chunk": reply,
                    },
                )
                yield encode_stream_event(
                    "TEXT_MESSAGE_CONTENT",
                    {
                        "chunk": reply,
                    },
                )

                await asyncio.to_thread(
                    record_run_event,
                    run_id,
                    "TEXT_MESSAGE_END",
                    {},
                )
                yield encode_stream_event("TEXT_MESSAGE_END")

                await asyncio.to_thread(
                    save_conversation_turn,
                    user_id=user_id,
                    session_id=session_id,
                    user_content=prompt,
                    assistant_content=reply,
                )

                history.append(
                    {
                        "role": "assistant",
                        "content": reply,
                    }
                )

                # 数据库和内存历史都已经保存完整 user/assistant，
                # 后续即使 Run 终态写入失败，也不能只撤销内存。
                turn_persisted = True

                max_saved_messages = MAX_ROUNDS * 2
                if len(history) > max_saved_messages + 1:
                    del history[1:-max_saved_messages]

                # 数据库和浏览器共用同一份指标 Payload
                finished_payload: dict[str, object] = build_run_finished_payload(result)

                await asyncio.to_thread(
                    finish_agent_run,
                    run_id,
                    "done",
                    finished_payload,
                )

                yield encode_stream_event(
                    "RUN_FINISHED",
                    finished_payload,
                )
                return

        raise RuntimeError("Agent Loop 未产生终态事件")

    except asyncio.CancelledError:
        if not turn_persisted:
            rollback_pending_turn(
                history,
                history_checkpoint,
            )

        cancel_reason = await asyncio.to_thread(
            get_run_cancellation_reason,
            run_id,
        )
        final_status = "error" if cancel_reason == "timeout" else "aborted"

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            final_status,
            {
                "reason": cancel_reason or "unknown",
            },
        )
        raise

    except OpenAIError:
        if not turn_persisted:
            rollback_pending_turn(
                history,
                history_checkpoint,
            )

        error_payload = {
            "code": "model_unavailable",
            "message": "模型服务暂时不可用",
        }

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            "error",
            error_payload,
        )

        # 流开始后不能再修改 HTTP 状态码，所以错误也必须进入事件流。
        yield encode_stream_event(
            "RUN_ERROR",
            error_payload,
        )

    except ModelDecisionError:
        if not turn_persisted:
            rollback_pending_turn(
                history,
                history_checkpoint,
            )

        error_payload = {
            "code": "invalid_model_decision",
            "message": "模型返回了无法处理的决策",
        }

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            "error",
            error_payload,
        )

        yield encode_stream_event(
            "RUN_ERROR",
            error_payload,
        )

    except Exception:  # noqa: BLE001 - 流已开始，只能转换为安全的终态事件。
        if not turn_persisted:
            rollback_pending_turn(
                history,
                history_checkpoint,
            )

        # 不把数据库错误、文件路径或执行器异常暴露给浏览器。
        error_payload = {
            "code": "agent_runtime_error",
            "message": "Agent 运行失败",
        }

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            "error",
            error_payload,
        )

        yield encode_stream_event(
            "RUN_ERROR",
            error_payload,
        )

    finally:
        cancellation_monitor.cancel()

        with suppress(asyncio.CancelledError):
            await cancellation_monitor


"""一轮聊天的业务编排：记忆恢复、模型调用和消息持久化。"""
