from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.repositories.auth.user_repository import (
    create_registered_user,
    get_or_create_user,
    get_user_by_username,
)
from app.schemas import RegisterRequest
from app.services.auth.password_service import hash_password, verify_password


def test_commit_persists_normalized_username_and_real_hash(engine):
    request = RegisterRequest.model_validate(
        {"username": " Agent_User ", "password": "Learning-Agent-2026!"}
    )
    encoded = hash_password(request.password.get_secret_value())
    with Session(engine) as session, session.begin():
        user = create_registered_user(
            session, username=request.username, password_hash=encoded
        )
        assert user.id is not None
        assert get_user_by_username(session, "agent_user") is user
        user_id = user.id
    with Session(engine) as session:
        stored = get_user_by_username(session, "agent_user")
        assert stored is not None
        assert stored.password_hash is not None
        assert stored.id == user_id
        assert stored.password_hash == encoded
        assert verify_password(
            request.password.get_secret_value(), stored.password_hash
        )


def test_unknown_username_returns_none(engine):
    with Session(engine) as session:
        assert get_user_by_username(session, "missing") is None


def test_server_generates_distinct_uuid4_identifiers(engine):
    with Session(engine) as session, session.begin():
        first = create_registered_user(
            session, username="first", password_hash="test-hash"
        )
        second = create_registered_user(
            session, username="second", password_hash="test-hash"
        )
        assert first.external_id != second.external_id
        assert first.id != second.id
        for user in (first, second):
            assert len(user.external_id) == 32
            assert UUID(hex=user.external_id).version == 4


def test_duplicate_username_does_not_modify_existing_user(engine):
    with Session(engine) as session, session.begin():
        user = create_registered_user(
            session, username="agent", password_hash="original-hash"
        )
        original_identity = (user.id, user.external_id)
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        create_registered_user(
            session, username="agent", password_hash="replacement-hash"
        )
    with Session(engine) as session:
        stored = get_user_by_username(session, "agent")
        assert stored is not None
        assert (stored.id, stored.external_id) == original_identity
        assert stored.password_hash == "original-hash"
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_explicit_rollback_removes_flushed_user(engine):
    with Session(engine) as session:
        user = create_registered_user(
            session, username="rolled_back", password_hash="test-hash"
        )
        assert user.id is not None
        session.rollback()
    with Session(engine) as session:
        assert get_user_by_username(session, "rolled_back") is None


def test_later_failure_rolls_back_entire_transaction(engine):
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        create_registered_user(
            session, username="agent", password_hash="first-hash"
        )
        create_registered_user(
            session, username="agent", password_hash="second-hash"
        )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_legacy_user_and_registered_user_coexist(engine):
    with Session(engine) as session, session.begin():
        legacy = get_or_create_user(session, "local-demo-user")
        registered = create_registered_user(
            session, username="agent", password_hash="test-hash"
        )
        assert legacy.id != registered.id
        assert legacy.username is None
        assert legacy.password_hash is None
        assert get_or_create_user(session, "local-demo-user") is legacy
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 2
