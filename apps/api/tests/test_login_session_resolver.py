"""Read-only login resolution against isolated PostgreSQL schemas.

Run from apps/api: python -m pytest -q tests/test_login_session_resolver.py
"""

import traceback
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from typing import NoReturn

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models import LoginSession, User
from app.repositories.login_session_repository import create_login_session, revoke_login_session
from app.repositories.user_repository import get_user_by_id
from app.schemas import LoginRequest, RegisterRequest
from app.services import login_session_resolver as resolver
from app.services.authentication_service import AuthenticatedUser
from app.services.login_session_service import issue_login_session
from app.services.registration_service import register_user


PASSWORD = "Resolver-Password-2026!"
TOKEN = SecretStr("A" * 43)
DIGEST = sha256(TOKEN.get_secret_value().encode()).hexdigest()
START = datetime(2026, 9, 14, 8, tzinfo=UTC)
END = START + timedelta(hours=8)


@pytest.fixture
def user_id(engine: Engine) -> int:
    with Session(engine) as session:
        identity = register_user(session, RegisterRequest.model_validate(
            {"username": "中文Agent", "password": PASSWORD},
        ))
        create_login_session(
            session, user_id=identity.id, token_hash=DIGEST,
            created_at=START, expires_at=END,
        )
        session.commit()
        return identity.id


def test_real_issuance_resolves_safe_chinese_identity(
    engine: Engine, user_id: int, caplog: pytest.LogCaptureFixture,
) -> None:
    with Session(engine) as session:
        issued = issue_login_session(session, LoginRequest.model_validate(
            {"username": " 中文AGENT ", "password": PASSWORD},
        ))
    with Session(engine) as session:
        identity = resolver.resolve_login_session(session, issued.token)
    assert identity == issued.user
    assert identity.id == user_id and identity.username == "中文agent"
    assert set(asdict(identity)) == {"id", "external_id", "username"}
    for secret in (issued.token.get_secret_value(), PASSWORD, "$argon2"):
        assert secret not in repr(identity) and secret not in caplog.text


@pytest.mark.parametrize("raw", [None, "", "A" * 42, "A" * 44, "A" * 42 + "=",
                                     "A" * 42 + " ", "A" * 42 + "\n", "中" * 43])
def test_invalid_format_never_queries_database(engine: Engine, raw: str | None) -> None:
    with Session(engine) as session:
        token = None if raw is None else SecretStr(raw)
        with pytest.raises(resolver.InvalidLoginSessionError) as caught:
            resolver.resolve_login_session(session, token)
        assert caught.value.code == "invalid_login_session"
        assert str(caught.value) == "登录状态无效，请重新登录"
        assert not session.in_transaction()


def test_unknown_token_is_redacted(
    engine: Engine, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    with Session(engine) as session:
        with pytest.raises(resolver.InvalidLoginSessionError) as caught:
            resolver.resolve_login_session(session, TOKEN, now=START)
        assert session.in_transaction()
    output = capsys.readouterr()
    for rendered in ("".join(traceback.format_exception(caught.value)),
                     caplog.text, output.out, output.err):
        assert TOKEN.get_secret_value() not in rendered
        assert PASSWORD not in rendered


@pytest.mark.parametrize("now,valid", [
    (START - timedelta(microseconds=1), False), (START, True),
    (END - timedelta(microseconds=1), True), (END, False),
    (END + timedelta(seconds=1), False),
    (START.astimezone(timezone(timedelta(hours=8))), True),
])
def test_time_boundaries(engine: Engine, user_id: int, now: datetime, valid: bool) -> None:
    with Session(engine) as session:
        if valid:
            assert resolver.resolve_login_session(session, TOKEN, now=now).id == user_id
        else:
            with pytest.raises(resolver.InvalidLoginSessionError):
                resolver.resolve_login_session(session, TOKEN, now=now)


def test_default_clock_uses_utc(engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return START

    monkeypatch.setattr(resolver, "datetime", Clock)
    with Session(engine) as session:
        assert resolver.resolve_login_session(session, TOKEN).id == user_id


def test_naive_time_remains_calling_error(engine: Engine, user_id: int):
    with Session(engine) as session:
        with pytest.raises(ValueError, match="时区"):
            resolver.resolve_login_session(session, TOKEN, now=START.replace(tzinfo=None))
        assert not session.in_transaction()


def test_committed_revocation_invalidates_token(engine: Engine, user_id: int):
    with Session(engine) as session:
        assert revoke_login_session(session, token_hash=DIGEST, now=START)
        session.commit()
    with Session(engine) as reader, pytest.raises(resolver.InvalidLoginSessionError):
        resolver.resolve_login_session(reader, TOKEN, now=START + timedelta(seconds=1))


@pytest.mark.parametrize("state", ["missing", "legacy", "missing_hash"])
def test_unusable_user_is_invalid_session(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    # FK/check constraints prevent some states in real PostgreSQL; inject repository results.
    user = None if state == "missing" else User(
        id=user_id, external_id="legacy", username=None if state == "legacy" else "中文agent",
        password_hash=None,
    )
    monkeypatch.setattr(resolver, "get_user_by_id", lambda session, user_id: user)
    with Session(engine) as session, pytest.raises(resolver.InvalidLoginSessionError):
        resolver.resolve_login_session(session, TOKEN, now=START)


@pytest.mark.parametrize("outcome", ["success", "invalid", "database_error"])
def test_caller_pending_changes_and_transaction_are_untouched(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    failure = OperationalError("SELECT", None, RuntimeError("synthetic database failure"))

    def fail_query(*args, **kwargs) -> NoReturn:
        raise failure

    def forbidden(*args, **kwargs) -> NoReturn:
        pytest.fail("Resolver must not flush, commit or rollback caller changes")

    with Session(engine, autoflush=True) as session:
        pending = User(external_id="caller-pending")
        session.add(pending)
        transaction = session.get_transaction()
        with monkeypatch.context() as patch:
            for method in ("flush", "commit", "rollback"):
                patch.setattr(session, method, forbidden)
            if outcome == "database_error":
                patch.setattr(resolver, "get_user_by_id", fail_query)
            if outcome == "success":
                assert resolver.resolve_login_session(session, TOKEN, now=START).id == user_id
            elif outcome == "invalid":
                with pytest.raises(resolver.InvalidLoginSessionError):
                    resolver.resolve_login_session(session, SecretStr("B" * 43), now=START)
            else:
                with pytest.raises(OperationalError) as caught:
                    resolver.resolve_login_session(session, TOKEN, now=START)
                assert caught.value is failure
        assert session.get_transaction() is transaction
        assert pending in session.new and pending.id is None
        assert session.autoflush is True
        with Session(engine) as reader:
            assert reader.scalar(select(User).where(User.external_id == "caller-pending")) is None
            assert reader.scalar(select(func.count()).select_from(LoginSession)) == 1
        session.commit()
    with Session(engine) as reader:
        assert reader.scalar(select(User).where(User.external_id == "caller-pending")) is not None


def test_second_query_cannot_flush_new_work(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = resolver.get_active_login_session
    pending = User(external_id="between-queries")

    def record(session: Session, *, token_hash: str, now: datetime):
        result = original(session, token_hash=token_hash, now=now)
        session.add(pending)
        return result

    monkeypatch.setattr(resolver, "get_active_login_session", record)
    with Session(engine, autoflush=True) as session:
        assert resolver.resolve_login_session(session, TOKEN, now=START).id == user_id
        assert pending in session.new and pending.id is None
        session.rollback()


@pytest.mark.parametrize("stage", ["get_active_login_session", "get_user_by_id"])
def test_real_database_failure_is_left_for_caller_to_rollback(
    engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    def fail_query(session: Session, *args, **kwargs):
        return session.execute(text("SELECT * FROM resolver_missing_test_table"))

    with Session(engine) as session:
        session.begin()
        transaction = session.get_transaction()
        with monkeypatch.context() as patch:
            patch.setattr(resolver, stage, fail_query)
            with pytest.raises(ProgrammingError):
                resolver.resolve_login_session(session, TOKEN, now=START)
        assert session.get_transaction() is transaction
        session.rollback()
        assert resolver.resolve_login_session(session, TOKEN, now=START).id == user_id


def test_user_lookup_returns_only_matching_id(engine: Engine, user_id: int):
    with Session(engine) as session:
        user = get_user_by_id(session, user_id)
        assert user is not None and user.username == "中文agent"
        assert get_user_by_id(session, -1) is None
        assert isinstance(resolver.resolve_login_session(session, TOKEN, now=START), AuthenticatedUser)
