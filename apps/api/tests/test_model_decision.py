import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.services.agent_runtime import (
    FinalAnswer,
    ToolAction,
    ToolErrorObservation,
    ToolObservation,
)
from app.services.model_decision import DeepSeekDecisionMaker, ModelDecisionError


def build_text_response(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=None,
                )
            )
        ]
    )


def build_tool_response(
    *tool_calls: tuple[str, str, str],
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=tool_call_id,
                            type="function",
                            function=SimpleNamespace(
                                name=tool_name,
                                arguments=arguments,
                            ),
                        )
                        for tool_call_id, tool_name, arguments in tool_calls
                    ],
                )
            )
        ]
    )


def build_decision_maker(
    *responses: SimpleNamespace,
    messages: list[ChatCompletionMessageParam] | None = None,
) -> tuple[DeepSeekDecisionMaker, AsyncMock, list[dict[str, Any]]]:
    response_iterator = iter(responses)
    requests: list[dict[str, Any]] = []

    async def create_completion(**kwargs):
        requests.append(deepcopy(kwargs))
        return next(response_iterator)

    create_mock = AsyncMock(side_effect=create_completion)
    fake_client = cast(
        AsyncOpenAI,
        SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create_mock),
            )
        ),
    )
    if messages is None:
        decision_maker = DeepSeekDecisionMaker(
            client=fake_client,
            model="deepseek-test",
            system_prompt="你是测试助手。",
            user_prompt="请解决这个问题。",
        )
    else:
        decision_maker = DeepSeekDecisionMaker(
            client=fake_client,
            model="deepseek-test",
            messages=messages,
        )
    return decision_maker, create_mock, requests


def test_decision_maker_returns_direct_final_answer():
    decision_maker, create_completion, requests = build_decision_maker(
        build_text_response("直接回答。"),
    )

    decision = asyncio.run(decision_maker(()))

    assert decision == FinalAnswer(content="直接回答。")
    assert create_completion.await_count == 1
    assert requests[0]["messages"] == [
        {"role": "system", "content": "你是测试助手。"},
        {"role": "user", "content": "请解决这个问题。"},
    ]


def test_decision_maker_copies_existing_chat_history():
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": "你是测试助手。"},
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
        {"role": "user", "content": "本轮问题"},
    ]
    original_messages = deepcopy(messages)
    decision_maker, _, requests = build_decision_maker(
        build_text_response("本轮回答"),
        messages=messages,
    )

    decision = asyncio.run(decision_maker(()))

    assert decision == FinalAnswer(content="本轮回答")
    assert requests[0]["messages"] == original_messages
    assert messages == original_messages


def test_decision_maker_preserves_tool_call_and_appends_result():
    decision_maker, _, requests = build_decision_maker(
        build_tool_response(
            (
                "call-area",
                "calculate_rectangle_area",
                '{"width": 3, "height": 4}',
            ),
        ),
        build_text_response("矩形面积是 12。"),
    )

    first_decision = asyncio.run(decision_maker(()))
    assert first_decision == ToolAction(
        tool_call_id="call-area",
        tool_name="calculate_rectangle_area",
        arguments='{"width": 3, "height": 4}',
    )

    observation = ToolObservation(
        tool_call_id="call-area",
        tool_name="calculate_rectangle_area",
        result="12",
    )
    second_decision = asyncio.run(decision_maker((observation,)))

    assert second_decision == FinalAnswer(content="矩形面积是 12。")
    assert requests[1]["messages"][-2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-area",
                "type": "function",
                "function": {
                    "name": "calculate_rectangle_area",
                    "arguments": '{"width": 3, "height": 4}',
                },
            }
        ],
    }
    assert requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-area",
        "content": "12",
    }


def test_decision_maker_serializes_structured_tool_error():
    decision_maker, _, requests = build_decision_maker(
        build_tool_response(("call-invalid", "get_current_time", "{}")),
        build_text_response("参数错误，无法查询。"),
    )

    asyncio.run(decision_maker(()))
    error_observation = ToolErrorObservation(
        tool_call_id="call-invalid",
        tool_name="get_current_time",
        code="invalid_tool_arguments",
        message="工具参数未通过校验",
        details="缺少 utc_offset_hours",
    )
    asyncio.run(decision_maker((error_observation,)))

    tool_message = requests[1]["messages"][-1]
    assert tool_message["tool_call_id"] == "call-invalid"
    assert json.loads(tool_message["content"]) == {
        "type": "tool_error",
        "tool_name": "get_current_time",
        "error": {
            "code": "invalid_tool_arguments",
            "message": "工具参数未通过校验",
            "details": "缺少 utc_offset_hours",
        },
    }


def test_decision_maker_does_not_append_same_observation_twice():
    decision_maker, _, requests = build_decision_maker(
        build_tool_response(("call-time", "get_current_time", "{}")),
        build_text_response("第一次回答。"),
        build_text_response("第二次回答。"),
    )

    asyncio.run(decision_maker(()))
    observation = ToolObservation(
        tool_call_id="call-time",
        tool_name="get_current_time",
        result="2026-09-08T12:00:00+08:00",
    )
    asyncio.run(decision_maker((observation,)))
    asyncio.run(decision_maker((observation,)))

    tool_messages = [
        message for message in requests[2]["messages"] if message["role"] == "tool"
    ]
    assert len(tool_messages) == 1


def test_decision_maker_rejects_rewritten_observation_history():
    decision_maker, create_completion, _ = build_decision_maker(
        build_tool_response(("call-time", "get_current_time", "{}")),
        build_text_response("第一次回答。"),
    )

    asyncio.run(decision_maker(()))
    original = ToolObservation(
        tool_call_id="call-time",
        tool_name="get_current_time",
        result="原始结果",
    )
    asyncio.run(decision_maker((original,)))

    rewritten = ToolObservation(
        tool_call_id="call-time",
        tool_name="get_current_time",
        result="被改写的结果",
    )
    with pytest.raises(ModelDecisionError, match="历史发生倒退或改写"):
        asyncio.run(decision_maker((rewritten,)))

    assert create_completion.await_count == 2


def test_decision_maker_explicitly_rejects_multiple_tool_calls():
    decision_maker, _, _ = build_decision_maker(
        build_tool_response(
            ("call-time", "get_current_time", '{"utc_offset_hours": 8}'),
            (
                "call-area",
                "calculate_rectangle_area",
                '{"width": 3, "height": 4}',
            ),
        )
    )

    with pytest.raises(ModelDecisionError, match="每轮只支持一个工具调用"):
        asyncio.run(decision_maker(()))


def test_decision_maker_rejects_empty_final_response():
    decision_maker, _, _ = build_decision_maker(build_text_response(None))

    with pytest.raises(ModelDecisionError, match="没有返回最终文本"):
        asyncio.run(decision_maker(()))


def test_decision_maker_rejects_response_without_choices():
    decision_maker, _, _ = build_decision_maker(
        SimpleNamespace(choices=[]),
    )

    with pytest.raises(ModelDecisionError, match="没有可用的 choice"):
        asyncio.run(decision_maker(()))
