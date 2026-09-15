"""Real PostgreSQL sessions at both chat boundaries; no external model calls."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import dependencies
from app.models import LoginSession
from app.routers.chat import chat
from app.schemas import LoginRequest, RegisterRequest
from app.services.auth.login_session_service import issue_login_session
from app.services.auth.registration_service import register_user


@pytest.fixture
def authenticated_chat(engine, monkeypatch):
    with Session(engine) as session:
        register_user(session, RegisterRequest(username="聊天测试", password="Chat-Test-2026!"))
    with Session(engine) as session:
        issued = issue_login_session(session, LoginRequest(username="聊天测试", password="Chat-Test-2026!"))
    sessions = []

    class TrackedSession(Session):
        closed = False

        def close(self):
            super().close()
            self.closed = True

    def factory():
        session = TrackedSession(engine)
        sessions.append(session)
        return session

    def check_closed(**kwargs):
        assert sessions and all(session.closed for session in sessions)
        return 123

    async def reply(**kwargs):
        check_closed()
        return "测试回复"

    async def stream(**kwargs):
        check_closed()
        yield '{"type":"RUN_FINISHED"}\n'

    create_run = Mock(side_effect=check_closed)
    model = AsyncMock(side_effect=reply)
    monkeypatch.setattr(dependencies, "SessionLocal", factory)
    monkeypatch.setattr(chat, "create_agent_run", create_run)
    monkeypatch.setattr(chat, "create_chat_reply", model)
    monkeypatch.setattr(chat, "stream_chat_reply", stream)
    app = FastAPI()
    app.include_router(chat.router)
    with TestClient(app) as client:
        yield client, issued.token.get_secret_value(), create_run, model
    assert all(session.closed for session in sessions)


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("state", ["valid", "missing", "malformed", "unknown", "expired", "revoked"])
def test_real_session_authentication(authenticated_chat, engine, path, state):
    client, token, create_run, model = authenticated_chat
    if state in {"expired", "revoked"}:
        with Session(engine) as session:
            record = session.scalar(select(LoginSession).where(
                LoginSession.token_hash == sha256(token.encode()).hexdigest(),
            ))
            now = datetime.now(UTC)
            if state == "expired":
                record.created_at = now - timedelta(hours=2)
                record.expires_at = now - timedelta(hours=1)
            else:
                record.revoked_at = now
            session.commit()
    cookie = {"missing": None, "malformed": "short", "unknown": "x" * 43}.get(state, token)
    headers = {"Origin": "http://localhost:3000"}
    if cookie is not None:
        headers["Cookie"] = f"agent_session={cookie}"
    response = client.post(path, headers=headers, json={"session_id": "test", "prompt": "你好"})
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    if state == "valid":
        assert response.status_code == 200
        if path.endswith("stream"):
            create_run.assert_called_once()
            assert response.headers["x-run-id"] == "123"
            assert response.text == '{"type":"RUN_FINISHED"}\n'
        else:
            model.assert_awaited_once()
            assert response.json() == {"reply": "测试回复"}
    else:
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_login_session"
        assert "x-run-id" not in response.headers
        create_run.assert_not_called()
        model.assert_not_called()


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_sql_failure_is_safe_and_prevents_work(authenticated_chat, monkeypatch, caplog, path):
    client, token, create_run, model = authenticated_chat

    def broken_query(session, token):
        session.execute(text("SELECT * FROM chat_auth_missing_table"))

    monkeypatch.setattr(dependencies, "resolve_login_session", broken_query)
    response = client.post(path, headers={"Origin": "http://localhost:3000", "Cookie": f"agent_session={token}"},
                           json={"session_id": "test", "prompt": "private prompt"})
    assert response.status_code == 500
    assert response.json()["code"] == "chat_failed"
    assert response.headers["cache-control"] == "no-store"
    for secret in (token, "private prompt", "chat_auth_missing_table"):
        assert secret not in response.text + caplog.text
    create_run.assert_not_called()
    model.assert_not_called()


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("body", ['{"prompt":"private prompt"}', '{'])
def test_input_errors_are_safe(authenticated_chat, path, body):
    client, token, create_run, model = authenticated_chat
    response = client.post(path, headers={"Origin": "http://localhost:3000", "Cookie": f"agent_session={token}",
                                         "Content-Type": "application/json"}, content=body)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_chat_input"
    assert "private prompt" not in response.text
    create_run.assert_not_called()
    model.assert_not_called()
