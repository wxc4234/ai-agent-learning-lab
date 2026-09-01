from unittest.mock import AsyncMock

import app.routers.chat as chat_router_module
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAIError


def test_chat_rejects_request_missing_required_fields():
    app = FastAPI()
    app.include_router(chat_router_module.router)
    client = TestClient(app)

    response = client.post("/chat", json={})

    assert response.status_code == 422


def test_chat_returns_friendly_error_when_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        chat_router_module,
        "create_chat_reply",
        AsyncMock(side_effect=OpenAIError("model unavailable")),
    )

    app = FastAPI()
    app.include_router(chat_router_module.router)
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={"session_id": "test-session", "prompt": "你好"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "模型服务暂时不可用"}
