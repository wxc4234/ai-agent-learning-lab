"""真实 PostgreSQL 验证占用快照、授权顺序及只读边界。"""

from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models import AgentRun, Conversation, ConversationExecutionSlot, Workspace
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError
from app.services.runtime import conversation_execution_query as query
from tests.runtime import test_conversation_execution_service as service_tests


# 复用本地 Task/Workspace 数据，底层由公共夹具创建随机数据库和私有 schema。
target = service_tests.target


@pytest.fixture
def reader(engine, target, monkeypatch):
    sessions = []
    factory = sessionmaker(bind=engine)

    def create_session():
        session = factory()
        sessions.append(session)
        return session

    monkeypatch.setattr(query, "SessionLocal", create_session)
    return sessions


@pytest.fixture
def sql_trace(engine):
    statements = []
    commits = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def capture_commit(conn):
        commits.append(True)

    event.listen(engine, "before_cursor_execute", capture)
    event.listen(engine, "commit", capture_commit)
    try:
        yield statements, commits
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(engine, "commit", capture_commit)


def read(target):
    return query.get_conversation_execution_status(user_id=target[0], session_id=target[1])


def test_acquire_release_and_detached_public_snapshot(engine, target, reader):
    empty = read(target)
    assert asdict(empty) == {"session_id": target[1], "occupied": False, "acquired_at": None}
    with Session(engine) as session:
        ownership = service_tests.acquire(session, target)
    occupied = read(target)
    assert asdict(occupied) == {
        "session_id": target[1], "occupied": True, "acquired_at": ownership.acquired_at,
    }
    assert occupied.acquired_at.tzinfo is not None
    with pytest.raises(FrozenInstanceError):
        occupied.occupied = False
    with Session(engine) as session:
        assert service_tests.release(session, target, ownership.owner_token)
    assert read(target) == empty
    # 已返回的普通数据保留旧快照；每次调用新建 Session，结束后不持有事务。
    assert occupied.occupied
    assert len(reader) == 3 and len({id(session) for session in reader}) == 3
    assert all(not session.in_transaction() for session in reader)


@pytest.mark.parametrize("kind", ["missing", "other-user", "standalone", "workspace-owner", "conversation-owner"])
def test_authorization_precedes_slot_access(engine, target, reader, kind, sql_trace):
    with Session(engine) as session:
        ownership = service_tests.acquire(session, target)
    user_id, session_id = target[:2]
    with engine.begin() as conn:
        if kind == "missing":
            session_id = "f" * 32
        elif kind == "other-user":
            user_id = target[2]
        elif kind == "standalone":
            conn.execute(update(Conversation).where(Conversation.external_id == session_id).values(task_id=None))
        elif kind == "workspace-owner":
            conn.execute(update(Workspace).values(user_id=target[2]))
        else:
            conn.execute(update(Conversation).where(Conversation.external_id == session_id).values(user_id=target[2]))
    statements, commits = sql_trace
    statements.clear()
    commits.clear()
    with pytest.raises(ConversationNotAccessibleError):
        query.get_conversation_execution_status(user_id=user_id, session_id=session_id)
    assert statements and not any("conversation_execution_slots" in sql for sql in statements)
    assert not commits and not reader[-1].in_transaction()
    assert service_tests.tokens(engine) == [ownership.owner_token]


@pytest.mark.parametrize("run_status", ["running", "finished", "aborted", "error"])
def test_old_slot_remains_occupied_independent_of_run(engine, target, reader, run_status, sql_trace):
    old_time = datetime(2000, 1, 1, tzinfo=UTC)
    with Session(engine) as session:
        ownership = service_tests.acquire(session, target)
    with Session(engine) as session, session.begin():
        conversation_id = session.scalar(select(Conversation.id).where(Conversation.external_id == target[1]))
        session.execute(update(ConversationExecutionSlot).values(acquired_at=old_time))
        session.add(AgentRun(conversation_id=conversation_id, status=run_status))
    statements, commits = sql_trace
    statements.clear()
    commits.clear()
    status = read(target)
    assert status.occupied and status.acquired_at == old_time
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not any(word in sql.lower() for sql in statements for word in ("owner_token", "for update", "agent_runs"))
    assert not commits and not reader[-1].in_transaction()
    # 独立连接确认原 token、原时间及 Run 状态均未被查询改变。
    with Session(engine) as session:
        slot = session.scalar(select(ConversationExecutionSlot))
        assert slot.owner_token == ownership.owner_token and slot.acquired_at == old_time
        assert session.scalar(select(AgentRun.status)) == run_status
    assert not query.get_conversation_execution_status(user_id=target[0], session_id=target[3]).occupied


@pytest.mark.parametrize("stage", ["authorization", "slot"])
def test_database_failure_propagates_and_ends_transaction(engine, target, reader, stage):
    failure = OperationalError("query", {}, RuntimeError("database unavailable"))

    def fail(conn, cursor, statement, parameters, context, executemany):
        if stage == "authorization" or "conversation_execution_slots" in statement:
            raise failure

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(OperationalError) as caught:
            read(target)
        assert caught.value is failure
        assert not reader[-1].in_transaction()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    # 故障后新的查询仍可正常工作，不把错误吞成 occupied=False。
    assert not read(target).occupied
