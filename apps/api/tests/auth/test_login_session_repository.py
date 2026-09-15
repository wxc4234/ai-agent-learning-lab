"""Login-session lifetime, constraints and real PostgreSQL transactions."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import LoginSession, User
from app.repositories.auth.login_session_repository import (
    create_login_session, get_active_login_session, revoke_login_session,
)

START = datetime(2026, 9, 13, 8, tzinfo=UTC)
END = START + timedelta(hours=8)
DIGEST = "a" * 64


@pytest.fixture
def user_id(engine: Engine) -> int:
    with Session(engine) as session:
        user = User(external_id="中文用户身份")
        session.add(user)
        session.commit()
        return user.id


def create(session: Session, user_id: int, digest: str = DIGEST) -> LoginSession:
    return create_login_session(
        session, user_id=user_id, token_hash=digest, created_at=START, expires_at=END,
    )


def test_create_commits_and_multiple_sessions_belong_to_user(engine: Engine, user_id: int):
    with Session(engine) as session:
        first = create(session, user_id)
        second = create(session, user_id, "b" * 64)
        assert first.id != second.id
        assert first.user_id == second.user_id == user_id
        session.commit()
    with Session(engine) as session:
        stored = get_active_login_session(session, token_hash=DIGEST, now=START)
        assert stored is not None
        assert stored.user_id == user_id and stored.token_hash == DIGEST
        assert stored.created_at == START and stored.expires_at == END
        assert stored.revoked_at is None


@pytest.mark.parametrize("now,active", [
    (START - timedelta(microseconds=1), False), (START, True),
    (END - timedelta(microseconds=1), True), (END, False),
    (END + timedelta(microseconds=1), False),
])
def test_expiration_boundaries(engine: Engine, user_id: int, now: datetime, active: bool):
    with Session(engine) as session:
        create(session, user_id)
        session.commit()
        assert (get_active_login_session(session, token_hash=DIGEST, now=now) is not None) == active


def test_create_rollback_does_not_persist(engine: Engine, user_id: int):
    with Session(engine) as session:
        create(session, user_id)
        session.rollback()
    with Session(engine) as session:
        assert get_active_login_session(session, token_hash=DIGEST, now=START) is None


def test_duplicate_digest_rolls_back_whole_transaction(engine: Engine, user_id: int):
    with Session(engine) as session:
        create(session, user_id)
        with pytest.raises(IntegrityError):
            create(session, user_id)
        session.rollback()
        assert get_active_login_session(session, token_hash=DIGEST, now=START) is None


def test_foreign_key_rejects_unknown_user(engine: Engine):
    with Session(engine) as session, pytest.raises(IntegrityError):
        create(session, -1)


@pytest.mark.parametrize("digest", ["", "x" * 64, "A" * 64, "a" * 63, "a" * 65])
def test_invalid_digest_is_rejected(engine: Engine, user_id: int, digest: str):
    with Session(engine) as session, pytest.raises(ValueError):
        create(session, user_id, digest)


@pytest.mark.parametrize("end", [START, START - timedelta(seconds=1)])
def test_invalid_expiration_is_rejected(engine: Engine, user_id: int, end: datetime):
    with Session(engine) as session, pytest.raises(ValueError):
        create_login_session(session, user_id=user_id, token_hash=DIGEST, created_at=START, expires_at=end)


@pytest.mark.parametrize("operation", ["create_start", "create_end", "query", "revoke"])
def test_naive_times_are_rejected(engine: Engine, user_id: int, operation: str):
    naive = START.replace(tzinfo=None)
    with Session(engine) as session, pytest.raises(ValueError, match="时区"):
        if operation == "query":
            get_active_login_session(session, token_hash=DIGEST, now=naive)
        elif operation == "revoke":
            revoke_login_session(session, token_hash=DIGEST, now=naive)
        else:
            create_login_session(
                session, user_id=user_id, token_hash=DIGEST,
                created_at=naive if operation == "create_start" else START,
                expires_at=naive if operation == "create_end" else END,
            )


def test_timezone_conversion(engine: Engine, user_id: int):
    zone = timezone(timedelta(hours=8))
    with Session(engine) as session:
        record = create_login_session(
            session, user_id=user_id, token_hash=DIGEST,
            created_at=START.astimezone(zone), expires_at=END.astimezone(zone),
        )
        assert record.created_at.tzinfo == UTC
        assert record.created_at == START


def test_revoke_is_idempotent_and_preserves_first_time(engine: Engine, user_id: int):
    revoked_at = START + timedelta(minutes=1)
    with Session(engine) as session:
        create(session, user_id)
        session.commit()
        assert revoke_login_session(session, token_hash=DIGEST, now=revoked_at)
        session.commit()
    with Session(engine) as session:
        assert get_active_login_session(session, token_hash=DIGEST, now=revoked_at) is None
        assert not revoke_login_session(session, token_hash=DIGEST, now=revoked_at + timedelta(minutes=1))
        session.commit()
        stored = session.query(LoginSession).filter_by(token_hash=DIGEST).one()
        assert stored.revoked_at == revoked_at


def test_revoke_rollback_restores_validity(engine: Engine, user_id: int):
    with Session(engine) as session:
        create(session, user_id)
        session.commit()
        assert revoke_login_session(session, token_hash=DIGEST, now=START)
        session.rollback()
    with Session(engine) as session:
        assert get_active_login_session(session, token_hash=DIGEST, now=START) is not None


def test_expired_session_can_still_be_marked_revoked(engine: Engine, user_id: int):
    with Session(engine) as session:
        create(session, user_id)
        session.commit()
        assert revoke_login_session(session, token_hash=DIGEST, now=END)


def test_unknown_digest_has_no_effect(engine: Engine):
    with Session(engine) as session:
        assert get_active_login_session(session, token_hash=DIGEST, now=START) is None
        assert not revoke_login_session(session, token_hash=DIGEST, now=START)


@pytest.mark.parametrize("field,value", [
    ("token_hash", "bad"), ("expires_at", START),
    ("revoked_at", START - timedelta(seconds=1)),
])
def test_database_checks_protect_direct_writes(engine: Engine, user_id: int, field: str, value: object):
    with Session(engine) as session, pytest.raises(IntegrityError):
        record = LoginSession(user_id=user_id, token_hash=DIGEST, created_at=START, expires_at=END)
        setattr(record, field, value)
        session.add(record)
        session.flush()
