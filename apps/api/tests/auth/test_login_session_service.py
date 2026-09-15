"""Session issuance with real PostgreSQL commits and isolated failure recovery."""

import traceback
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import NoReturn

import pytest
from argon2.exceptions import InvalidHashError
from psycopg.errors import UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import LoginSession, User
from app.schemas import LoginRequest, RegisterRequest
from app.services.auth import login_session_service as service
from app.services.auth.authentication_service import InvalidCredentialsError
from app.services.auth.registration_service import register_user


PASSWORD = "Login-Lesson-2026!"
START = datetime(2026, 9, 14, 8, tzinfo=UTC)


def request(username: str = "中文agent", password: str = PASSWORD) -> LoginRequest:
    return LoginRequest.model_validate({"username": username, "password": password})


@pytest.fixture
def user_id(engine: Engine) -> int:
    with Session(engine) as session:
        return register_user(session, RegisterRequest.model_validate(
            {"username": "中文Agent", "password": PASSWORD},
        )).id


def count_sessions(engine: Engine) -> int:
    with Session(engine) as reader:
        return reader.scalar(select(func.count()).select_from(LoginSession)) or 0


@pytest.mark.parametrize("ttl", [None, timedelta(minutes=15)])
def test_success_commits_safe_result_and_exact_lifetime(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, ttl: timedelta | None,
) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return START

    monkeypatch.setattr(service, "datetime", Clock)
    with Session(engine) as session:
        if ttl is None:
            result = service.issue_login_session(session, request(" 中文AGENT "))
        else:
            result = service.issue_login_session(session, request(), ttl=ttl)
        assert not session.in_transaction()
        token = result.token.get_secret_value()
        assert result.user.id == user_id
        assert result.user.username == "中文agent"
        assert result.expires_at == START + (ttl or timedelta(hours=8))
        assert token not in repr(result)
        assert token not in str(result.token)
        assert PASSWORD not in repr(result)
        assert token not in caplog.text and PASSWORD not in caplog.text
        # Reader uses a distinct physical connection while the writer holds its own.
        writer_connection = session.connection()
        with Session(engine) as reader:
            assert reader.connection().connection.driver_connection is not (
                writer_connection.connection.driver_connection
            )
            stored = reader.scalar(select(LoginSession))
            assert stored is not None
            assert stored.user_id == user_id
            assert stored.created_at == START
            assert stored.expires_at == result.expires_at
            assert stored.revoked_at is None
            assert stored.token_hash == sha256(token.encode("utf-8")).hexdigest()
            values = [getattr(stored, column.key) for column in LoginSession.__table__.columns]
            assert token not in values and PASSWORD not in values


def test_repeated_login_creates_distinct_credentials(engine: Engine, user_id: int) -> None:
    with Session(engine) as session:
        first = service.issue_login_session(session, request())
        second = service.issue_login_session(session, request())
    assert first.token.get_secret_value() != second.token.get_secret_value()
    assert count_sessions(engine) == 2


@pytest.mark.parametrize("username,password", [
    ("中文agent", "wrong"), ("不存在用户", PASSWORD), ("中文agent", PASSWORD + " "),
])
def test_invalid_credentials_roll_back_and_do_not_generate_token(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, username: str, password: str,
) -> None:
    def forbidden_token(nbytes: int) -> NoReturn:
        pytest.fail("Invalid credentials must not reach token generation")

    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(service, "token_urlsafe", forbidden_token)
            with pytest.raises(InvalidCredentialsError) as caught:
                service.issue_login_session(session, request(username, password))
        assert not session.in_transaction() and session.is_active
        assert count_sessions(engine) == 0
        rendered = "".join(traceback.format_exception(caught.value))
        assert password not in rendered and password not in caplog.text
        service.issue_login_session(session, request())


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-1)])
def test_invalid_ttl_has_no_transaction_or_writes(engine: Engine, ttl: timedelta) -> None:
    with Session(engine) as session:
        with pytest.raises(ValueError, match="有效期必须大于零"):
            service.issue_login_session(session, request(), ttl=ttl)
        assert not session.in_transaction()
    assert count_sessions(engine) == 0


@pytest.mark.parametrize("caller_state", ["pending", "flushed", "read"])
def test_caller_transaction_is_preserved(engine: Engine, caller_state: str) -> None:
    with Session(engine) as session:
        user = User(external_id="caller-owned")
        if caller_state == "read":
            session.scalar(select(User))
        else:
            session.add(user)
            if caller_state == "flushed":
                session.flush()
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match="无活动事务"):
            service.issue_login_session(session, request())
        assert session.get_transaction() is transaction
        assert transaction is not None and transaction.is_active
        if caller_state == "pending":
            assert user in session.new
        session.commit()
    with Session(engine) as reader:
        found = reader.scalar(select(User).where(User.external_id == "caller-owned"))
        assert (found is not None) == (caller_state != "read")
    assert count_sessions(engine) == 0


@pytest.mark.parametrize("failure_kind", ["integrity", "connection"])
def test_commit_failure_removes_flushed_row_and_session_recovers(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch, failure_kind: str,
) -> None:
    error_type = IntegrityError if failure_kind == "integrity" else OperationalError
    failure = error_type("COMMIT", None, RuntimeError("synthetic failure"))
    with Session(engine) as session:
        def fail_commit() -> NoReturn:
            assert session.scalar(select(func.count()).select_from(LoginSession)) == 1
            assert count_sessions(engine) == 0
            raise failure

        with monkeypatch.context() as patch:
            patch.setattr(session, "commit", fail_commit)
            with pytest.raises(error_type) as caught:
                service.issue_login_session(session, request())
            assert caught.value is failure
        assert not session.in_transaction() and session.is_active
        assert count_sessions(engine) == 0
        service.issue_login_session(session, request())
    assert count_sessions(engine) == 1


def test_real_duplicate_digest_rolls_back_without_exposing_raw_credentials(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    token = "synthetic-secret-token-for-constraint-test"

    def fixed_token(nbytes: int) -> str:
        assert nbytes == 32
        return token

    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(service, "token_urlsafe", fixed_token)
            service.issue_login_session(session, request())
            with pytest.raises(IntegrityError) as caught:
                service.issue_login_session(session, request())
        assert isinstance(caught.value.orig, UniqueViolation)
        assert not session.in_transaction() and session.is_active
        assert count_sessions(engine) == 1
        output = capsys.readouterr()
        for text in ("".join(traceback.format_exception(caught.value)),
                     caplog.text, output.out, output.err):
            assert token not in text and PASSWORD not in text and "$argon2" not in text
        service.issue_login_session(session, request())
    assert count_sessions(engine) == 2


def test_random_generator_failure_rolls_back_authentication_query(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("synthetic random source failure")

    def fail_token(nbytes: int) -> NoReturn:
        raise failure

    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(service, "token_urlsafe", fail_token)
            with pytest.raises(RuntimeError) as caught:
                service.issue_login_session(session, request())
            assert caught.value is failure
        assert not session.in_transaction() and count_sessions(engine) == 0
        service.issue_login_session(session, request())


def test_corrupt_hash_remains_system_failure(engine: Engine, user_id: int) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        assert user is not None
        user.password_hash = "broken-hash"
        session.commit()
        with pytest.raises(InvalidHashError):
            service.issue_login_session(session, request())
        assert not session.in_transaction() and session.is_active
    assert count_sessions(engine) == 0
