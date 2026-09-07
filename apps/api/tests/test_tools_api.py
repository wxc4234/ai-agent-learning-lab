from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.tools as tools_router_module


def build_tool_call_response(
    *,
    tool_call_id: str,
    tool_name: str,
    arguments: str,
) -> SimpleNamespace:
    """构造一次模型请求工具的最小响应。"""
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
                    ],
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
) -> AsyncMock:
    """按顺序返回固定模型响应，避免消耗 API 配额。"""
    create_completion = AsyncMock(side_effect=responses)
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create_completion),
        )
    )
    monkeypatch.setattr(tools_router_module, "client", fake_client)
    return create_completion


def test_tool_test_returns_traceable_error_for_unknown_tool(monkeypatch):
    create_completion = replace_model_responses(
        monkeypatch,
        build_tool_call_response(
            tool_call_id="call-unknown",
            tool_name="read_secret_file",
            arguments="{}",
        ),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "读取服务器密钥"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "tool_error",
        "tool_call_id": "call-unknown",
        "tool_name": "read_secret_file",
        "error": {
            "code": "unknown_tool",
            "message": "工具未注册：read_secret_file",
        },
    }
    create_completion.assert_awaited_once()


def test_tool_test_returns_traceable_error_for_invalid_arguments(monkeypatch):
    create_completion = replace_model_responses(
        monkeypatch,
        build_tool_call_response(
            tool_call_id="call-invalid",
            tool_name="get_current_time",
            arguments='{"utc_offset_hours": 15}',
        ),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "查询 UTC+15 的时间"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "tool_error"
    assert body["tool_call_id"] == "call-invalid"
    assert body["tool_name"] == "get_current_time"
    assert body["error"]["code"] == "invalid_tool_arguments"
    assert body["error"]["details"][0]["loc"] == ["utc_offset_hours"]
    assert body["error"]["details"][0]["type"] == "less_than_equal"
    create_completion.assert_awaited_once()


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


def test_tool_test_executes_new_registered_tool_without_router_changes(
    monkeypatch,
):
    create_completion = replace_model_responses(
        monkeypatch,
        build_tool_call_response(
            tool_call_id="call-area",
            tool_name="calculate_rectangle_area",
            arguments='{"width": 3, "height": 4}',
        ),
        build_text_response("矩形面积是 12 平方单位"),
    )

    response = create_test_client().post(
        "/tool-test",
        json={"prompt": "宽 3、高 4 的矩形面积是多少？"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "type": "final_answer",
        "tool_name": "calculate_rectangle_area",
        "tool_result": "12",
        "reply": "矩形面积是 12 平方单位",
    }
    assert create_completion.await_count == 2

    second_request = create_completion.await_args_list[1].kwargs
    assert second_request["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-area",
        "content": "12",
    }
