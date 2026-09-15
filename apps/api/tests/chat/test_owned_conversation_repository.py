"""Ownership and transaction behavior on the shared isolated PostgreSQL fixture."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.models import Conversation, User
from app.repositories.chat.conversation_repository import (
    ConversationNotAccessibleError,
    get_or_create_owned_conversation,
    require_owned_conversation,
)


@pytest.fixture
def owners(engine):
    with Session(engine) as session, session.begin():
        users = [User(external_id="owner-a"), User(external_id="owner-b")]
        session.add_all(users)
        session.flush()
        return tuple(user.id for user in users)


def test_creation_reuse_and_owner_read(engine, owners):
    with Session(engine) as session, session.begin():
        first = get_or_create_owned_conversation(session, user_id=owners[0], session_id="会话一")
        first.title = "保留标题"
        session.flush()
        second = get_or_create_owned_conversation(session, user_id=owners[0], session_id="会话一")
        assert second.id == first.id
        assert second.user_id == owners[0]
        assert second.title == "保留标题"
        identifier = first.id
    with Session(engine) as session:
        found = require_owned_conversation(session, user_id=owners[0], session_id="会话一")
        assert found.id == identifier
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1


@pytest.mark.parametrize("operation", [require_owned_conversation, get_or_create_owned_conversation])
def test_other_owner_cannot_read_or_take_over(engine, owners, operation):
    with Session(engine) as session, session.begin():
        record = get_or_create_owned_conversation(session, user_id=owners[0], session_id="private-id")
        record.title = "private-title"
    with Session(engine) as session, session.begin():
        with pytest.raises(ConversationNotAccessibleError) as error:
            operation(session, user_id=owners[1], session_id="private-id")
        assert error.value.code == "conversation_not_accessible"
        assert str(error.value) == "会话不存在或不可访问"
        # A handled business denial does not poison the caller's transaction.
        assert session.scalar(text("SELECT 1")) == 1
    with Session(engine) as session:
        record = session.scalar(select(Conversation))
        assert record.user_id == owners[0]
        assert record.title == "private-title"
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1


def test_missing_and_foreign_read_have_identical_errors_and_do_not_create(engine, owners):
    with Session(engine) as session, session.begin():
        get_or_create_owned_conversation(session, user_id=owners[0], session_id="existing")
    errors = []
    with Session(engine) as session, session.begin():
        for identifier in ("existing", "missing"):
            with pytest.raises(ConversationNotAccessibleError) as error:
                require_owned_conversation(session, user_id=owners[1], session_id=identifier)
            errors.append((type(error.value), str(error.value), error.value.code))
        assert errors[0] == errors[1]
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1


@pytest.mark.parametrize("commit", [True, False])
def test_caller_controls_visibility_and_commit(engine, owners, commit):
    with Session(engine) as writer:
        get_or_create_owned_conversation(writer, user_id=owners[0], session_id="pending")
        assert writer.in_transaction()
        with Session(engine) as observer:
            assert observer.scalar(select(func.count()).select_from(Conversation)) == 0
        if commit:
            writer.commit()
        else:
            writer.rollback()
    with Session(engine) as observer:
        assert observer.scalar(select(func.count()).select_from(Conversation)) == int(commit)


def test_outer_error_rolls_back_conversation_and_other_work(engine, owners):
    with (
        Session(engine) as session,
        pytest.raises(RuntimeError, match="later failure"),
        session.begin(),
    ):
        session.add(User(external_id="caller-pending-user"))
        get_or_create_owned_conversation(session, user_id=owners[0], session_id="rolled-back")
        raise RuntimeError("later failure")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(User).where(User.external_id == "caller-pending-user")) is None


def test_foreign_key_error_is_not_swallowed_and_caller_can_rollback(engine, owners):
    with Session(engine) as session:
        with pytest.raises(IntegrityError) as error:
            get_or_create_owned_conversation(session, user_id=max(owners) + 1000, session_id="bad-owner")
        assert error.value.orig.sqlstate == "23503"
        session.rollback()
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0


def test_unrelated_unique_constraint_is_not_swallowed(engine, owners):
    with engine.begin() as connection:
        # This extra constraint exists only in this test's private schema.
        connection.execute(text("ALTER TABLE conversations ADD CONSTRAINT test_unique_owner UNIQUE (user_id)"))
    with Session(engine) as session, session.begin():
        get_or_create_owned_conversation(session, user_id=owners[0], session_id="one")
    with Session(engine) as session:
        with pytest.raises(IntegrityError) as error:
            get_or_create_owned_conversation(session, user_id=owners[0], session_id="two")
        assert error.value.orig.sqlstate == "23505"
        assert error.value.orig.diag.constraint_name == "test_unique_owner"
        session.rollback()
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1


@pytest.mark.parametrize("operation", [require_owned_conversation, get_or_create_owned_conversation])
def test_sql_failure_keeps_database_classification(engine, owners, operation):
    with Session(engine) as session:
        session.execute(text("SET LOCAL search_path TO pg_catalog"))
        with pytest.raises(ProgrammingError) as error:
            operation(session, user_id=owners[0], session_id="failure")
        assert error.value.orig.sqlstate == "42P01"
        session.rollback()
        assert session.scalar(text("SELECT 1")) == 1


@pytest.mark.parametrize("same_owner", [True, False])
@pytest.mark.parametrize("first_commits", [True, False])
def test_concurrent_same_identifier_never_overwrites_owner(engine, owners, same_owner, first_commits):
    second_owner = owners[0] if same_owner else owners[1]
    ready = Event()
    worker_pid = []

    def contender():
        with Session(engine) as session:
            try:
                with session.begin():
                    session.execute(text("SET LOCAL statement_timeout = '8000ms'"))
                    worker_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
                    ready.set()
                    record = get_or_create_owned_conversation(
                        session, user_id=second_owner, session_id="contended",
                    )
                    result = (record.id, record.user_id)
                return result
            except ConversationNotAccessibleError:
                return "denied"

    with Session(engine) as first, ThreadPoolExecutor(max_workers=1) as executor:
        try:
            assert first.connection().get_isolation_level() == "READ COMMITTED"
            first_pid = first.scalar(text("SELECT pg_backend_pid()"))
            created = get_or_create_owned_conversation(first, user_id=owners[0], session_id="contended")
            created_id = created.id
            future = executor.submit(contender)
            assert ready.wait(3), "Contender did not start"
            deadline = monotonic() + 5
            with engine.connect() as observer:
                while True:
                    blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": worker_pid[0]})
                    if first_pid in blockers:
                        break
                    assert not future.done(), "Contender must wait for the uncommitted unique key"
                    assert monotonic() < deadline, "PostgreSQL did not report the expected lock wait"
                    sleep(0.01)
            if first_commits:
                first.commit()
            else:
                first.rollback()
            outcome = future.result(timeout=5)
        finally:
            # Release the first transaction even when an assertion fails, before joining.
            first.rollback()
    with Session(engine) as session:
        record = session.scalar(select(Conversation))
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1
        if first_commits:
            assert record.id == created_id and record.user_id == owners[0]
            assert outcome == ((created_id, owners[0]) if same_owner else "denied")
        else:
            assert record.user_id == second_owner
            assert outcome == (record.id, second_owner)
