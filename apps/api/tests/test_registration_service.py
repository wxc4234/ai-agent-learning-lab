"""Registration tests use real commits in an isolated PostgreSQL database."""

import traceback
from dataclasses import asdict
from typing import NoReturn
from uuid import uuid4

import pytest
from psycopg import Error as PsycopgError
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import User
from app.repositories.user_repository import get_user_by_username
from app.schemas import RegisterRequest
from app.services import registration_service as service
from app.services.password_service import verify_password


def request(username: str = "agent_user") -> RegisterRequest:
    return RegisterRequest.model_validate(
        {"username": username, "password": "Learning-Agent-2026!"}
    )


def test_success_commits_and_returns_only_identity(engine: Engine) -> None:
    payload = request(" Agent_User ")
    with Session(engine) as session:
        result = service.register_user(session, payload)
        assert not session.in_transaction()
    assert set(asdict(result)) == {"id", "external_id", "username"}
    assert result.username == "agent_user"
    assert payload.password.get_secret_value() not in repr(result)
    with Session(engine) as session:
        stored = get_user_by_username(session, result.username)
        assert stored is not None
        assert stored.password_hash is not None
        assert stored.id == result.id
        assert stored.external_id == result.external_id
        assert verify_password(payload.password.get_secret_value(), stored.password_hash)
        assert stored.password_hash not in repr(result)


def test_existing_transaction_is_not_committed_or_rolled_back(engine: Engine) -> None:
    with Session(engine) as session:
        pending = User(external_id="caller-owned")
        session.add(pending)
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match="无活动事务"):
            service.register_user(session, request())
        assert session.get_transaction() is transaction
        assert pending in session.new
        session.commit()
        user_id = pending.id
    with Session(engine) as session:
        assert session.get(User, user_id) is not None


def test_duplicate_is_classified_and_session_recovers(engine: Engine) -> None:
    with Session(engine) as session:
        service.register_user(session, request())
        with pytest.raises(service.UsernameAlreadyExistsError):
            service.register_user(session, request())
        assert not session.in_transaction()
        assert session.is_active
        service.register_user(session, request("second_user"))


@pytest.mark.parametrize("failure_kind", ["integrity", "connection"])
def test_commit_failure_rolls_back_flushed_user(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, failure_kind: str,
) -> None:
    error_type = IntegrityError if failure_kind == "integrity" else OperationalError
    failure = error_type("COMMIT", None, RuntimeError("synthetic failure"))
    with Session(engine) as session:
        with monkeypatch.context() as patch:
            def fail_commit() -> NoReturn:
                raise failure

            patch.setattr(session, "commit", fail_commit)
            with pytest.raises(error_type) as caught:
                service.register_user(session, request())
            assert caught.value is failure
        assert not session.in_transaction()
        assert session.is_active
        with Session(engine) as reader:
            assert get_user_by_username(reader, "agent_user") is None
        service.register_user(session, request())


def test_hash_failure_propagates_without_creating_user(
    engine: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("synthetic hash failure")

    def fail_hash(password: str) -> NoReturn:
        raise failure

    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(service, "hash_password", fail_hash)
            with pytest.raises(RuntimeError) as caught:
                service.register_user(session, request())
            assert caught.value is failure
        assert not session.in_transaction()
        service.register_user(session, request())


def test_postgres_duplicate_rolls_back_and_session_recovers(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        first = service.register_user(session, request())
        original = get_user_by_username(session, "agent_user")
        assert original is not None
        original_hash = original.password_hash
        assert original_hash is not None
        session.rollback()
        with pytest.raises(service.UsernameAlreadyExistsError) as caught:
            service.register_user(session, request(" AGENT_USER "))
        assert caught.value.code == "username_already_exists"
        assert not session.in_transaction()
        assert session.is_active
        second = service.register_user(session, request("second_user"))
        assert second.id != first.id
        stored = get_user_by_username(session, "agent_user")
        assert stored is not None
        assert stored.id == first.id
        assert stored.password_hash == original_hash


def test_postgres_conflict_traceback_does_not_expose_database_details(
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with Session(engine) as session:
        service.register_user(session, request())
        with pytest.raises(service.UsernameAlreadyExistsError) as caught:
            service.register_user(session, request())
        rendered = "".join(traceback.format_exception(caught.value))
        output = capsys.readouterr()
        for exposed in (rendered, caplog.text, output.out, output.err):
            contains_hash = "$argon2id$" in exposed
            contains_sql = "INSERT INTO users" in exposed
            contains_password = request().password.get_secret_value() in exposed
            assert not contains_hash, "Password hash exposed in error output"
            assert not contains_sql, "SQL exposed in error output"
            assert not contains_password, "Password exposed in error output"
        assert caught.value.__cause__ is None


@pytest.mark.parametrize("violation", ["external_id", "credentials_pair"])
def test_postgres_other_constraints_are_not_username_conflicts(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    violation: str,
) -> None:
    with Session(engine) as session:
        first = service.register_user(session, request())
        observed: list[IntegrityError] = []

        def invalid_insert(
            session: Session, *, username: str, password_hash: str,
        ) -> User:
            user = User(
                external_id=first.external_id if violation == "external_id" else uuid4().hex,
                username=username,
                password_hash=password_hash if violation == "external_id" else None,
            )
            session.add(user)
            try:
                session.flush()
            except IntegrityError as error:
                observed.append(error)
                raise
            return user

        with monkeypatch.context() as patch:
            patch.setattr(service, "create_registered_user", invalid_insert)
            with pytest.raises(IntegrityError) as caught:
                service.register_user(session, request("second_user"))
            assert caught.value is observed[0]
            expected = "23505" if violation == "external_id" else "23514"
            original = caught.value.orig
            assert isinstance(original, PsycopgError)
            assert original.sqlstate == expected
            assert original.diag.constraint_name != "ix_users_username"
        assert not session.in_transaction()
        assert session.is_active
        service.register_user(session, request("second_user"))
