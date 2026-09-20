"""本地真实 HTTP 与隔离 PostgreSQL 验证执行占用查询边界。"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import AgentRun, AgentRunEvent, Conversation, ConversationExecutionSlot, Message, User, Workspace
from app.routers.chat import conversation
from app.services.runtime import conversation_execution_query as query
from app.services.runtime.conversation_execution_service import (
    acquire_conversation_execution,
    release_conversation_execution,
)
from tests.local import test_local_mode as local_tests
from tests.local.test_local_mode import HEADERS


local_client = local_tests.local_client


@pytest.fixture
def lab(local_client, engine, monkeypatch):
    monkeypatch.setattr(query, "SessionLocal", sessionmaker(bind=engine))
    response = local_client.post("/workspaces", headers=HEADERS, json={"name": "查询项目"})
    assert response.status_code == 201
    workspace = response.json()
    response = local_client.post(
        f"/workspaces/{workspace['external_id']}/tasks", headers=HEADERS, json={"title": "查询任务"},
    )
    assert response.status_code == 201
    session_id = response.json()["conversation_id"]
    with Session(engine) as session:
        user_id = session.scalar(select(Workspace.user_id))
        other = User(external_id="other-query-user")
        session.add(other)
        session.commit()
        other_id = other.id
    return local_client, user_id, session_id, other_id


def snapshot(engine):
    # 独立读取关键业务表，验证请求不会创建运行/消息或改变原占用。
    with Session(engine) as session:
        counts = tuple(session.scalar(select(func.count()).select_from(model))
                       for model in (Conversation, Message, AgentRun, AgentRunEvent))
        slots = list(session.execute(select(
            ConversationExecutionSlot.conversation_id,
            ConversationExecutionSlot.owner_token,
            ConversationExecutionSlot.acquired_at,
        )))
    return counts, slots


def get(lab, **kwargs):
    return lab[0].get(f"/sessions/{lab[2]}/execution", headers=HEADERS, **kwargs)


def test_empty_occupied_released_and_public_contract(lab, engine):
    client, user_id, session_id, _ = lab
    expected = {"session_id": session_id, "occupied": False, "acquired_at": None}
    before = snapshot(engine)
    response = get(lab)
    assert response.status_code == 200 and response.json() == expected
    assert response.headers["cache-control"] == "no-store"
    assert snapshot(engine) == before
    with Session(engine) as session:
        owner = acquire_conversation_execution(session, user_id=user_id, session_id=session_id)
    before = snapshot(engine)
    response = get(lab)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == set(expected) and payload["occupied"] is True
    assert payload["session_id"] == session_id
    assert datetime.fromisoformat(payload["acquired_at"].replace("Z", "+00:00")) == owner.acquired_at
    assert owner.owner_token not in response.text
    assert response.headers["cache-control"] == "no-store" and "x-run-id" not in response.headers
    assert snapshot(engine) == before
    with Session(engine) as session:
        assert release_conversation_execution(session, user_id=user_id, session_id=session_id, owner_token=owner.owner_token)
    assert get(lab).json() == expected
    # 路由确实注册在主应用，公开模型只定义三个字段且允许 acquired_at 为 null。
    schema = client.get("/openapi.json", headers=HEADERS).json()
    assert "get" in schema["paths"]["/sessions/{session_id}/execution"]
    model = schema["components"]["schemas"]["ConversationExecutionStatusResponse"]
    assert set(model["properties"]) == set(expected)
    assert set(model["required"]) == set(expected)


@pytest.mark.parametrize("kind", ["missing", "standalone", "foreign-conversation", "foreign-workspace"])
def test_inaccessible_is_safe_404_before_slot_query(lab, engine, kind):
    client, user_id, session_id, other_id = lab
    with Session(engine) as session:
        acquire_conversation_execution(session, user_id=user_id, session_id=session_id)
    with engine.begin() as conn:
        if kind == "missing":
            session_id = "missing-private-session"
        elif kind == "standalone":
            conn.execute(update(Conversation).values(task_id=None))
        elif kind == "foreign-conversation":
            conn.execute(update(Conversation).values(user_id=other_id))
        else:
            conn.execute(update(Workspace).values(user_id=other_id))
    before = snapshot(engine)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        # 客户端伪造的 user_id 不能替代身份依赖。
        response = client.get(f"/sessions/{session_id}/execution?user_id={other_id}", headers=HEADERS)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 404
    assert response.json() == {"code": "conversation_not_accessible", "message": "会话不存在或不可访问"}
    assert response.headers["cache-control"] == "no-store"
    assert not any("conversation_execution_slots" in sql for sql in statements)
    assert snapshot(engine) == before


@pytest.mark.parametrize("headers", [
    {}, {"X-Local-Runtime-Token": "b" * 64},
    HEADERS | {"Origin": "https://evil.test"}, HEADERS | {"Host": "evil.test"},
])
def test_local_boundary_rejects_before_query(lab, engine, monkeypatch, headers):
    def forbidden(**kwargs):
        pytest.fail("untrusted request entered query")

    monkeypatch.setattr(conversation, "get_conversation_execution_status", forbidden)
    before = snapshot(engine)
    response = lab[0].get(f"/sessions/{lab[2]}/execution", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "local_access_rejected"
    assert response.headers["cache-control"] == "no-store"
    assert snapshot(engine) == before


def test_query_uses_worker_and_trusted_identity(lab, monkeypatch):
    real = conversation.get_conversation_execution_status
    observed = []

    def checked(**kwargs):
        # 线程中没有运行中的 asyncio 事件循环；同时执行真实数据库查询。
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        observed.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(conversation, "get_conversation_execution_status", checked)
    assert get(lab, params={"user_id": lab[3]}).status_code == 200
    assert observed == [{"user_id": lab[1], "session_id": lab[2]}]


def test_real_database_error_is_safe_500_not_idle(lab, engine, monkeypatch, caplog):
    def failure(**kwargs):
        with Session(engine) as session:
            session.execute(text("SELECT * FROM private_execution_missing_table"))

    monkeypatch.setattr(conversation, "get_conversation_execution_status", failure)
    before = snapshot(engine)
    response = get(lab)
    assert response.status_code == 500
    assert response.json() == {"code": "chat_failed", "message": "聊天服务暂时出错"}
    assert response.headers["cache-control"] == "no-store"
    assert "private_execution_missing_table" not in response.text + caplog.text
    assert "occupied" not in response.json()
    assert snapshot(engine) == before


@pytest.mark.parametrize("occupied", [False, True])
def test_run_state_does_not_replace_slot_state(lab, engine, occupied):
    _, user_id, session_id, _ = lab
    old_time = datetime(2000, 1, 1, tzinfo=UTC)
    if occupied:
        with Session(engine) as session:
            acquire_conversation_execution(session, user_id=user_id, session_id=session_id)
    with Session(engine) as session, session.begin():
        conversation_id = session.scalar(select(Conversation.id))
        session.add(AgentRun(conversation_id=conversation_id, status="aborted" if occupied else "running"))
        if occupied:
            session.execute(update(ConversationExecutionSlot).values(acquired_at=old_time))
    before = snapshot(engine)
    response = get(lab)
    assert response.status_code == 200 and response.json()["occupied"] is occupied
    if occupied:
        assert datetime.fromisoformat(response.json()["acquired_at"].replace("Z", "+00:00")) == old_time
    else:
        assert response.json()["acquired_at"] is None
    assert snapshot(engine) == before
