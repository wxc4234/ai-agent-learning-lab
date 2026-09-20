from app.services.runtime.execution.execution_budget import ExecutionBudget
"""Chat authentication must finish before creating a run or invoking a model."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import require_current_user
from app.services.auth.authentication_service import AuthenticatedUser
from app.routers.chat import chat, chat_execution
from app.services.auth.login_session_resolver import InvalidLoginSessionError


@pytest.fixture
def boundary(monkeypatch, execution_stub):
    application = FastAPI()
    application.state.execution_budget = ExecutionBudget(capacity=2)
    application.include_router(chat.router)
    create_run = Mock(return_value=123)
    reply = AsyncMock(return_value="测试回复")
    stream_calls = []

    async def stream(**kwargs):
        stream_calls.append(kwargs)
        yield '{"type":"RUN_FINISHED"}\n'

    monkeypatch.setattr(chat_execution, "create_agent_run", create_run)
    monkeypatch.setattr(chat, "create_chat_reply", reply)
    monkeypatch.setattr(chat, "stream_chat_reply", stream)
    with TestClient(application) as client:
        yield application, client, create_run, reply, stream_calls


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("failure,status,code", [
    (InvalidLoginSessionError, 401, "invalid_login_session"),
    (RuntimeError, 500, "chat_failed"),
])
def test_auth_failure_prevents_all_business_work(boundary, path, failure, status, code):
    application, client, create_run, reply, stream_calls = boundary
    calls = []

    def reject():
        calls.append(True)
        raise failure()

    application.dependency_overrides[require_current_user] = reject
    response = client.post(path, headers={"Origin": "http://localhost:3000"},
                           json={"session_id": "test", "prompt": "你好"})
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"
    assert "x-run-id" not in response.headers
    assert calls == [True]
    create_run.assert_not_called()
    reply.assert_not_called()
    assert not stream_calls


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("headers,status", [
    ({}, 403),
    ({"Origin": "http://localhost:3000.evil.test"}, 403),
    ({"Origin": "null"}, 403),
    ({"Origin": "http://localhost:3000", "Content-Type": "text/plain"}, 415),
])
def test_origin_and_content_type_rejected_before_auth(boundary, path, headers, status):
    application, client, create_run, reply, stream_calls = boundary
    calls = []

    def authenticate():
        calls.append(True)
        return AuthenticatedUser(1, "test-user", "tester")

    application.dependency_overrides[require_current_user] = authenticate
    response = client.post(path, headers=headers, content='{"prompt":"private"}')
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text
    assert calls == []
    create_run.assert_not_called()
    reply.assert_not_called()
    assert not stream_calls


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_success_still_requires_authentication(boundary, path):
    application, client, create_run, reply, stream_calls = boundary
    calls = []

    def authenticate():
        calls.append(True)
        return AuthenticatedUser(1, "test-user", "tester")

    application.dependency_overrides[require_current_user] = authenticate
    response = client.post(path, headers={"Origin": "http://localhost:3000"},
                           json={"session_id": "test", "prompt": "你好"})
    assert response.status_code == 200
    assert calls == [True]
    assert response.headers["cache-control"] == "no-store"
    if path.endswith("stream"):
        assert response.headers["x-run-id"] == "123"
        assert response.headers["content-type"] == "application/x-ndjson"
        assert response.text == '{"type":"RUN_FINISHED"}\n'
        create_run.assert_called_once()
        assert len(stream_calls) == 1
    else:
        assert response.json() == {"reply": "测试回复"}
        reply.assert_awaited_once()
