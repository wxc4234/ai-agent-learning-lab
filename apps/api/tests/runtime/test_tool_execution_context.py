"""可信工具上下文：隔离 PostgreSQL、模式无关授权与只读清理。"""

from dataclasses import FrozenInstanceError, asdict

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Workspace
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError
from app.services.runtime import tool_execution_context as service
from app.tools.context import ToolExecutionContext
from tests.tasks import test_task_deletion_service as task_fixtures


# 复用已提交的用户、项目、任务和会话，底层仍使用根目录隔离数据库夹具。
target = task_fixtures.target


@pytest.fixture
def database(engine, target, monkeypatch):
    sessions = []
    statements = []

    class ReadSession(Session):
        closed = False

        def commit(self):
            pytest.fail("context query must not commit")

        def close(self):
            super().close()
            self.closed = True

    def factory():
        session = ReadSession(engine)
        sessions.append(session)
        return session

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(service, "SessionLocal", factory)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield sessions, statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        assert sessions
        assert all(session.closed and not session.in_transaction() for session in sessions)


def load(target, **overrides):
    args = {key: target[key] for key in ("user_id", "conversation_id")}
    return service.load_tool_execution_context(**(args | overrides))


@pytest.mark.parametrize("mode", ["local", "account"])
def test_owned_context_is_plain_and_read_only(database, target, monkeypatch, mode):
    monkeypatch.setattr(settings, "app_mode", mode)
    result = load(target)
    assert asdict(result) == {
        key: target[key]
        for key in ("user_id", "conversation_id", "workspace_id", "task_id")
    }
    assert database[0][0].closed
    assert len(database[1]) == 1
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])
    assert not hasattr(result, "root_path")
    assert not hasattr(result, "__dict__")


@pytest.mark.parametrize("mode", ["local", "account"])
@pytest.mark.parametrize("kind", [
    "missing", "wrong-user", "foreign-conversation", "foreign-workspace", "no-task",
])
def test_full_ownership_required_in_both_modes(engine, database, target, monkeypatch, mode, kind):
    monkeypatch.setattr(settings, "app_mode", mode)
    overrides = {}
    with Session(engine) as session, session.begin():
        if kind == "missing":
            overrides["conversation_id"] = "f" * 32
        elif kind == "wrong-user":
            overrides["user_id"] = target["other_id"]
        elif kind == "foreign-conversation":
            session.get(Conversation, target["conversation_pk"]).user_id = target["other_id"]
        elif kind == "foreign-workspace":
            session.scalar(select(Workspace)).user_id = target["other_id"]
        else:
            session.get(Conversation, target["conversation_pk"]).task_id = None
    database[1].clear()
    with pytest.raises(ConversationNotAccessibleError) as caught:
        load(target, **overrides)
    assert caught.value.code == "conversation_not_accessible"
    assert str(caught.value) == "会话不存在或不可访问"
    assert database[1] and all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])


@pytest.mark.parametrize("root_path", [None, "/nonexistent-context-test-directory"])
def test_binding_and_filesystem_do_not_determine_context(engine, database, target, root_path):
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = root_path
    result = load(target)
    assert result.task_id == target["task_id"]
    assert result.workspace_id == target["workspace_id"]


def test_same_owner_other_conversation_uses_its_own_task(database, target):
    result = load(target, conversation_id="e" * 32)
    assert result.task_id == "d" * 32
    assert result.conversation_id == "e" * 32
    assert result.workspace_id == target["workspace_id"]


def test_second_workspace_is_derived_from_current_conversation(engine, database, target):
    from app.models import Task

    with Session(engine) as session, session.begin():
        workspace = Workspace(external_id="f" * 32, name="second", user_id=target["user_id"])
        task = Task(external_id="9" * 32, title="second task", workspace=workspace)
        conversation = Conversation(external_id="8" * 32, user_id=target["user_id"], task=task)
        session.add(conversation)
    first = load(target)
    second = load(target, conversation_id="8" * 32)
    assert first.workspace_id == target["workspace_id"]
    assert (second.workspace_id, second.task_id) == ("f" * 32, "9" * 32)
    assert first is not second


@pytest.mark.parametrize("field", ["user_id", "conversation_id", "workspace_id", "task_id"])
def test_context_fields_are_frozen(field):
    context = ToolExecutionContext(user_id=1, conversation_id="c", workspace_id="w", task_id="t")
    with pytest.raises(FrozenInstanceError):
        setattr(context, field, "changed")


@pytest.mark.parametrize("field", ["workspace_id", "task_id", "root_path"])
def test_loader_does_not_accept_independent_resource_selection(field):
    with pytest.raises(TypeError):
        service.load_tool_execution_context(user_id=1, conversation_id="c", **{field: "forged"})


def test_real_database_failure_propagates_and_closes(database, target, monkeypatch):
    session = service.SessionLocal()
    original_execute = session.execute

    def broken_execute(*args, **kwargs):
        return original_execute(text("SELECT * FROM missing_context_test_table"))

    monkeypatch.setattr(session, "execute", broken_execute)
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    with pytest.raises(DBAPIError):
        load(target)
    assert session.closed
    assert not session.in_transaction()
