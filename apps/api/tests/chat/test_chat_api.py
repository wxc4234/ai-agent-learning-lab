from app.services.runtime.execution_budget import ExecutionBudget
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAIError

import app.routers.chat.chat as chat_router_module
from app.dependencies import require_current_user
from app.services.auth.authentication_service import AuthenticatedUser


def test_chat_rejects_request_missing_required_fields():
    app = FastAPI()
    app.state.execution_budget = ExecutionBudget(capacity=2)
    app.include_router(chat_router_module.router)
    app.dependency_overrides[require_current_user] = lambda: AuthenticatedUser(1, "test-user", "tester")
    client = TestClient(app)

    response = client.post("/chat", json={}, headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 422


def test_chat_returns_friendly_error_when_model_is_unavailable(monkeypatch, execution_stub):
    monkeypatch.setattr(
        chat_router_module,
        "create_chat_reply",
        AsyncMock(side_effect=OpenAIError("model unavailable")),
    )

    app = FastAPI()
    app.state.execution_budget = ExecutionBudget(capacity=2)
    app.include_router(chat_router_module.router)
    app.dependency_overrides[require_current_user] = lambda: AuthenticatedUser(1, "test-user", "tester")
    client = TestClient(app)

    response = client.post(
        "/chat",
        headers={"Origin": "http://localhost:3000"},
        json={"session_id": "test-session", "prompt": "你好"},
    )

    assert response.status_code == 502
    assert response.json() == {"code": "model_service_unavailable", "message": "模型服务暂时不可用"}
