import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

import app.routers.tools as tools_router_module


def build_tool_call_response(
    *,
    tool_call_id: str,
    tool_name: str,
    arguments: str,
) -> SimpleNamespace:
    """构造一次模型请求工具的最小响应。"""
    return build_tool_calls_response(
        (tool_call_id, tool_name, arguments),
    )


def build_tool_calls_response(
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


def build_text_response(content: str) -> SimpleNamespace:
    """构造模型生成最终文本的最小响应。"""
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


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(tools_router_module.router)
    return TestClient(app)


def replace_model_responses(
    monkeypatch,
    *responses: SimpleNamespace,
) -> tuple[AsyncMock, list[dict[str, Any]]]:
    """按顺序返回固定模型响应，并保存每次请求当时的消息快照。"""
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
    monkeypatch.setattr(tools_router_module, "client", fake_client)
    return create_mock, requests


def test_tool_test_returns_direct_final_answer(monkeypatch):
    create_completion, requests = replace_model_responses(
        monkeypatch,
        build_text_response("你好，我可以直接回答。"),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "你好"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "final_answer",
        "reply": "你好，我可以直接回答。",
        "steps_taken": 1,
        "observations": [],
    }
    assert create_completion.await_count == 1
    assert requests[0]["messages"][1] == {
        "role": "user",
        "content": "你好",
    }


def test_tool_test_runs_model_tool_model_loop(monkeypatch):
    create_completion, requests = replace_model_responses(
        monkeypatch,
        build_tool_call_response(
            tool_call_id="call-area",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
        ),
        build_text_response("矩形面积是 12 平方单位。"),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "宽 3、高 4 的矩形面积是多少？"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "final_answer",
        "reply": "矩形面积是 12 平方单位。",
        "steps_taken": 2,
        "observations": [
            {
                "tool_call_id": "call-area",
                "tool_name": "calculate_rectangle_area",
                "result": "12",
            }
        ],
    }
    assert create_completion.await_count == 2

    second_messages = requests[1]["messages"]
    assert second_messages[-2]["tool_calls"][0] == {
        "id": "call-area",
        "type": "function",
        "function": {
            "name": "calculate_rectangle_area",
            "arguments": '{"width": 3, "height": 4}',
        },
    }
    assert second_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call-area",
        "content": "12",
    }


def test_tool_test_feeds_unknown_tool_error_back_to_model(monkeypatch):
    _, requests = replace_model_responses(
        monkeypatch,
        build_tool_call_response(
            tool_call_id="call-unknown",
            tool_name="read_secret_file",
            arguments="{}",
        ),
        build_text_response("这个工具不可用，我无法读取服务器密钥。"),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "读取服务器密钥"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "final_answer"
    assert body["steps_taken"] == 2
    assert body["observations"] == [
        {
            "tool_call_id": "call-unknown",
            "tool_name": "read_secret_file",
            "code": "unknown_tool",
            "message": "工具未注册：read_secret_file",
            "details": None,
        }
    ]

    tool_message = requests[1]["messages"][-1]
    assert tool_message["tool_call_id"] == "call-unknown"
    assert json.loads(tool_message["content"]) == {
        "type": "tool_error",
        "tool_name": "read_secret_file",
        "error": {
            "code": "unknown_tool",
            "message": "工具未注册：read_secret_file",
        },
    }


def test_tool_test_feeds_invalid_arguments_back_to_model(monkeypatch):
    _, requests = replace_model_responses(
        monkeypatch,
        build_tool_call_response(
            tool_call_id="call-invalid",
            tool_name="get_current_time",
            arguments='{"utc_offset_hours": 15}',
        ),
        build_text_response("UTC 时差最大支持到 +14。"),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "查询 UTC+15 的时间"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "final_answer"
    assert body["observations"][0]["code"] == "invalid_tool_arguments"
    assert "utc_offset_hours" in body["observations"][0]["details"]

    error_payload = json.loads(requests[1]["messages"][-1]["content"])
    assert error_payload["error"]["code"] == "invalid_tool_arguments"
    assert "utc_offset_hours" in error_payload["error"]["details"]


def test_tool_test_rejects_multiple_tool_calls(monkeypatch):
    create_completion, _ = replace_model_responses(
        monkeypatch,
        build_tool_calls_response(
            (
                "call-area",
                "calculate_rectangle_area",
                '{"width": 3, "height": 4}',
            ),
            ("call-time", "get_current_time", '{"utc_offset_hours": 8}'),
        ),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "同时算面积和查询时间"},
    )

    assert response.status_code == 502
    assert "每轮只支持一个工具调用" in response.json()["detail"]
    assert create_completion.await_count == 1


def test_tool_test_returns_explicit_max_steps_result(monkeypatch):
    responses = tuple(
        build_tool_call_response(
            tool_call_id=f"call-{index}",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
        )
        for index in range(1, 6)
    )
    create_completion, _ = replace_model_responses(monkeypatch, *responses)

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "一直调用工具"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "max_steps_exceeded"
    assert body["reply"] is None
    assert body["steps_taken"] == 5
    assert len(body["observations"]) == 5
    assert create_completion.await_count == 5
