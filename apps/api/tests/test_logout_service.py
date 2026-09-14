"""Logout transactions on isolated PostgreSQL; run: python -m pytest -q tests/test_logout_service.py."""

import traceback
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from typing import NoReturn

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models import LoginSession, User
from app.repositories.login_session_repository import create_login_session
from app.schemas import LoginRequest, RegisterRequest
from app.services import logout_service as service
from app.services.login_session_resolver import InvalidLoginSessionError, resolve_login_session
from app.services.login_session_service import issue_login_session
from app.services.registration_service import register_user


PASSWORD = "Logout-Password-2026!"
TOKEN = SecretStr("A" * 43)
DIGEST = sha256(TOKEN.get_secret_value().encode()).hexdigest()
START = datetime(2026, 9, 14, 8, tzinfo=UTC)
END = START + timedelta(hours=8)


@pytest.fixture
def user_id(engine: Engine) -> int:
    with Session(engine) as session:
        user = register_user(session, RegisterRequest.model_validate(
            {"username": "中文Agent", "password": PASSWORD},
        ))
        create_login_session(session, user_id=user.id, token_hash=DIGEST,
            created_at=START, expires_at=END)
        session.commit()
        return user.id


def revoked_at(engine: Engine) -> datetime | None:
    with Session(engine) as reader:
        row = reader.scalar(select(LoginSession).where(LoginSession.token_hash == DIGEST))
        assert row is not None
        return row.revoked_at


def test_real_issuance_logout_rejects_only_target_session(engine: Engine, user_id: int):
    request = LoginRequest.model_validate({"username": " 中文AGENT ", "password": PASSWORD})
    with Session(engine) as writer:
        first = issue_login_session(writer, request)
        second = issue_login_session(writer, request)
        assert service.logout_user(writer, first.token) is True
        assert not writer.in_transaction()
        # Hold a new writer connection so the reader necessarily uses another connection.
        connection = writer.connection()
        with Session(engine) as reader:
            assert reader.connection().connection.driver_connection is not connection.connection.driver_connection
            with pytest.raises(InvalidLoginSessionError):
                resolve_login_session(reader, first.token)
            assert resolve_login_session(reader, second.token).id == user_id


@pytest.mark.parametrize("now", [START, END, END + timedelta(days=1),
                                   START.astimezone(timezone(timedelta(hours=8)))])
def test_revocation_time_and_expired_records(engine: Engine, user_id: int, now: datetime):
    with Session(engine) as session:
        assert service.logout_user(session, TOKEN, now=now) is True
        assert not session.in_transaction()
    assert revoked_at(engine) == now


def test_repeat_preserves_first_timestamp(engine: Engine, user_id: int):
    with Session(engine) as session:
        assert service.logout_user(session, TOKEN, now=START) is True
        assert service.logout_user(session, TOKEN, now=END) is False
        assert not session.in_transaction()
    assert revoked_at(engine) == START


def test_before_creation_does_not_revoke(engine: Engine, user_id: int):
    with Session(engine) as session:
        assert service.logout_user(session, TOKEN, now=START - timedelta(seconds=1)) is False
        assert not session.in_transaction()
    assert revoked_at(engine) is None


@pytest.mark.parametrize("raw", [None, "", "A" * 42, "A" * 44, "A" * 42 + "=",
                                     "A" * 42 + " ", "A" * 42 + "\n", "中" * 43])
def test_invalid_input_never_starts_transaction(engine: Engine, raw: str | None):
    with Session(engine) as session:
        token = None if raw is None else SecretStr(raw)
        assert service.logout_user(session, token) is False
        assert not session.in_transaction()


def test_unknown_token_ends_transaction(engine: Engine, user_id: int):
    with Session(engine) as session:
        assert service.logout_user(session, SecretStr("B" * 43), now=START) is False
        assert not session.in_transaction()
    assert revoked_at(engine) is None


@pytest.mark.parametrize("state", ["pending", "flushed", "read"])
@pytest.mark.parametrize("token", [TOKEN, None], ids=["present", "missing"])
def test_caller_transaction_untouched(
    engine: Engine, user_id: int, state: str, token: SecretStr | None,
):
    with Session(engine) as session:
        pending = User(external_id="caller-owned")
        if state == "read":
            session.scalar(select(User))
        else:
            session.add(pending)
            if state == "flushed":
                session.flush()
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match="无活动事务"):
            service.logout_user(session, token, now=START)
        assert session.get_transaction() is transaction
        assert transaction is not None and transaction.is_active
        if state == "pending":
            assert pending in session.new
        session.commit()
    with Session(engine) as reader:
        found = reader.scalar(select(User).where(User.external_id == "caller-owned"))
        assert (found is not None) == (state != "read")
    assert revoked_at(engine) is None


def test_commit_failure_rolls_back_and_token_stays_valid(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
):
    failure = OperationalError("COMMIT", None, RuntimeError("synthetic commit failure"))
    with Session(engine) as session:
        def fail_commit() -> NoReturn:
            stored = session.scalar(select(LoginSession).where(LoginSession.token_hash == DIGEST))
            assert stored is not None and stored.revoked_at == START
            assert revoked_at(engine) is None
            raise failure

        with monkeypatch.context() as patch:
            patch.setattr(session, "commit", fail_commit)
            with pytest.raises(OperationalError) as caught:
                service.logout_user(session, TOKEN, now=START)
            assert caught.value is failure
        assert not session.in_transaction() and session.is_active
        assert revoked_at(engine) is None
        with Session(engine) as reader:
            assert resolve_login_session(reader, TOKEN, now=START).id == user_id
        output = capsys.readouterr()
        for rendered in ("".join(traceback.format_exception(caught.value)),
                         caplog.text, output.out, output.err):
            assert TOKEN.get_secret_value() not in rendered and PASSWORD not in rendered
        assert service.logout_user(session, TOKEN, now=START) is True


def test_real_sql_failure_rolls_back_and_session_recovers(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch,
):
    original = service.revoke_login_session

    def fail_after_update(session: Session, *, token_hash: str, now: datetime):
        assert original(session, token_hash=token_hash, now=now)
        session.execute(text("SELECT * FROM logout_missing_test_table"))
        return True

    with Session(engine) as session:
        with monkeypatch.context() as patch:
            patch.setattr(service, "revoke_login_session", fail_after_update)
            with pytest.raises(ProgrammingError):
                service.logout_user(session, TOKEN, now=START)
        assert not session.in_transaction() and session.is_active
        assert revoked_at(engine) is None
        assert service.logout_user(session, TOKEN, now=START) is True


def test_naive_time_is_calling_error(engine: Engine, user_id: int):
    with Session(engine) as session:
        with pytest.raises(ValueError, match="时区"):
            service.logout_user(session, TOKEN, now=START.replace(tzinfo=None))
        assert not session.in_transaction()
        assert service.logout_user(session, TOKEN, now=START) is True


def test_default_clock_uses_utc(engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return START

    monkeypatch.setattr(service, "datetime", Clock)
    with Session(engine) as session:
        assert service.logout_user(session, TOKEN) is True
    assert revoked_at(engine) == START
