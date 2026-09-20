from app.services.runtime.execution_budget import ExecutionBudget
from app.services.runtime import conversation_execution_scope
"""Real HTTP identities, ownership, cache and persistence with model calls mocked."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import dependencies
from app.models import AgentRun, AgentRunEvent, Conversation, Message, User
from app.repositories.chat import conversation_repository as conversations
from app.repositories.runtime import run_repository as runs
from app.routers.chat import chat
from app.routers.chat import conversation
from app.schemas import LoginRequest, RegisterRequest
from app.services.chat import chat_service
from app.services.runtime.agent_runtime import FinalAnswer
from app.services.auth.login_session_service import issue_login_session
from app.services.auth.registration_service import register_user


@pytest.fixture
def lab(engine, monkeypatch):
    factory = sessionmaker(engine)
    for module in (dependencies, conversations, runs, conversation_execution_scope):
        monkeypatch.setattr(module, "SessionLocal", factory)
    identities = []
    for name in ("用户甲", "用户乙"):
        with Session(engine) as session:
            register_user(session, RegisterRequest(username=name, password="Ownership-Test-2026!"))
        with Session(engine) as session:
            identities.append(issue_login_session(session, LoginRequest(username=name, password="Ownership-Test-2026!")))
    prompts = []

    class DecisionMaker:
        def __init__(self, **kwargs):
            prompts.append(deepcopy(kwargs["messages"]))

        async def __call__(self, observations):
            return FinalAnswer(content="模型回复")

    async def completion(**kwargs):
        prompts.append(deepcopy(kwargs["messages"]))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="模型回复"))])

    async def wait(*args):
        await asyncio.Future()

    monkeypatch.setattr(chat_service, "DeepSeekDecisionMaker", DecisionMaker)
    monkeypatch.setattr(chat_service.client.chat.completions, "create", AsyncMock(side_effect=completion))
    monkeypatch.setattr(chat_service, "wait_for_run_cancellation", wait)
    chat_service.conversations.clear()
    app = FastAPI()
    app.state.execution_budget = ExecutionBudget(capacity=2)
    app.include_router(chat.router)
    app.include_router(conversation.router)
    with TestClient(app) as client:
        yield client, identities, prompts
    chat_service.conversations.clear()


def headers(identity):
    return {"Origin": "http://localhost:3000", "Cookie": f"agent_session={identity.token.get_secret_value()}"}


def send(client, identity, path, session_id, prompt="私密问题甲"):
    return client.post(path, headers=headers(identity), json={
        "session_id": session_id, "prompt": prompt, "user_id": 99999,
    })


def counts(engine):
    with Session(engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (Conversation, Message, AgentRun, AgentRunEvent))


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_owner_reuses_history_and_other_user_is_denied_before_side_effects(lab, engine, path):
    client, (a, b), prompts = lab
    response = send(client, a, path, "shared-id")
    assert response.status_code == 200
    if path.endswith("stream"):
        assert '"RUN_FINISHED"' in response.text
    with Session(engine) as session:
        record = session.scalar(select(Conversation))
        assert record.user_id == a.user.id
    before = counts(engine)
    cache_before = deepcopy(chat_service.conversations)
    rejected = send(client, b, path, "shared-id", "不应该执行")
    assert rejected.status_code == 404
    assert rejected.json()["code"] == "conversation_not_accessible"
    assert rejected.headers["cache-control"] == "no-store"
    assert "x-run-id" not in rejected.headers
    assert len(prompts) == 1
    assert counts(engine) == before
    assert chat_service.conversations == cache_before
    assert send(client, a, path, "shared-id", "继续甲").status_code == 200
    assert any(m.get("content") == "私密问题甲" for m in prompts[-1])
    assert send(client, b, path, "own-b", "乙的问题").status_code == 200
    assert not any(m.get("content") == "私密问题甲" for m in prompts[-1])
    # 请求收尾丢弃缓存；上面的提示词与下面的数据库历史验证恢复及隔离。
    assert chat_service.conversations == {}
    history = client.get("/sessions/shared-id/messages", headers=headers(a))
    assert history.status_code == 200 and history.json()["total"] == 4
    assert client.get("/sessions/shared-id/messages", headers=headers(b)).status_code == 404
    assert client.get("/sessions/shared-id/messages").status_code == 401


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_cache_entry_never_bypasses_database_owner_check(lab, engine, path):
    client, (a, b), prompts = lab
    conversations.ensure_owned_conversation(user_id=a.user.id, session_id="owned-a")
    poisoned_key = (b.user.id, "owned-a")
    chat_service.conversations[poisoned_key] = [{"role": "system", "content": "poisoned"}]
    before = counts(engine)
    assert send(client, b, path, "owned-a").status_code == 404
    assert prompts == [] and counts(engine) == before
    assert chat_service.conversations[poisoned_key] == [{"role": "system", "content": "poisoned"}]


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_restart_loads_only_owned_persisted_history(lab, path):
    client, (a, _), prompts = lab
    assert send(client, a, path, "restart").status_code == 200
    chat_service.conversations.clear()
    assert send(client, a, path, "restart", "恢复问题").status_code == 200
    assert [m["content"] for m in prompts[-1]][1:] == ["私密问题甲", "模型回复", "恢复问题"]


def test_empty_owned_history_is_200_missing_and_foreign_are_same_404(lab):
    client, (a, b), _ = lab
    conversations.ensure_owned_conversation(user_id=a.user.id, session_id="empty")
    response = client.get("/sessions/empty/messages", headers=headers(a))
    assert response.json() == {"session_id": "empty", "total": 0, "messages": []}
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    foreign = client.get("/sessions/empty/messages", headers=headers(b))
    missing = client.get("/sessions/missing/messages", headers=headers(b))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


@pytest.mark.parametrize("operation", ["load", "save"])
def test_repository_rejects_foreign_read_and_write(lab, engine, operation):
    _, (a, b), _ = lab
    conversations.ensure_owned_conversation(user_id=a.user.id, session_id="repo")
    before = counts(engine)
    with pytest.raises(conversations.ConversationNotAccessibleError):
        if operation == "load":
            conversations.load_conversation(user_id=b.user.id, session_id="repo")
        else:
            conversations.save_conversation_turn(user_id=b.user.id, session_id="repo", user_content="bad", assistant_content="bad")
    assert counts(engine) == before


def test_legacy_anonymous_conversation_cannot_be_claimed(lab, engine):
    client, (a, _), prompts = lab
    with Session(engine) as session, session.begin():
        old = User(external_id="local-demo-user")
        session.add(old)
        session.flush()
        session.add(Conversation(user_id=old.id, external_id="legacy"))
    before = counts(engine)
    assert send(client, a, "/chat/stream", "legacy").status_code == 404
    assert counts(engine) == before and prompts == []


def test_history_database_failure_is_safe(lab, engine, monkeypatch, caplog):
    client, (a, _), _ = lab

    def failure(**kwargs):
        with Session(engine) as session:
            session.execute(text("SELECT * FROM private_missing_history_table"))

    monkeypatch.setattr(conversation, "load_conversation", failure)
    response = client.get("/sessions/private-id/messages", headers=headers(a))
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert "private_missing_history_table" not in response.text + caplog.text


def test_failed_history_load_does_not_publish_partial_cache(lab, monkeypatch):
    client, (a, _), prompts = lab

    def failure(**kwargs):
        raise RuntimeError("private failure")

    monkeypatch.setattr(chat_service, "load_conversation", failure)
    assert send(client, a, "/chat", "load-failure").status_code == 500
    assert (a.user.id, "load-failure") not in chat_service.conversations
    assert prompts == []
