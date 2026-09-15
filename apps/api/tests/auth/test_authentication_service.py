"""Credential verification against PostgreSQL, with no session issuance."""

from dataclasses import asdict
from typing import NoReturn

import pytest
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import User
from app.routers.auth import registration
from app.schemas import LoginRequest, RegisterRequest
from app.services.auth import authentication_service as auth
from app.services.auth.password_service import hash_password
from app.services.auth.registration_service import register_user

PASSWORD = "Login-Password-2026!"


def login(username: str = "中文Agent", password: str = PASSWORD) -> LoginRequest:
    return LoginRequest.model_validate({"username": username, "password": password})


@pytest.fixture
def user_id(engine: Engine) -> int:
    with Session(engine) as session:
        result = register_user(session, RegisterRequest.model_validate(
            {"username": "中文Agent", "password": PASSWORD},
        ))
        return result.id


def test_success_returns_safe_identity(engine: Engine, user_id: int) -> None:
    with Session(engine) as session:
        identity = auth.authenticate_user(session, login(" 中文AGENT "))
        assert identity.id == user_id
        assert identity.username == "中文agent"
        assert set(asdict(identity)) == {"id", "external_id", "username"}
        assert PASSWORD not in repr(identity)
        assert "$argon2" not in repr(identity)


@pytest.mark.parametrize("username,password", [
    ("中文agent", "wrong"), ("不存在用户", PASSWORD), ("中文agent", PASSWORD + " "),
])
def test_invalid_credentials_have_one_public_error(
    engine: Engine, user_id: int, username: str, password: str,
) -> None:
    with Session(engine) as session, pytest.raises(auth.InvalidCredentialsError) as error:
        auth.authenticate_user(session, login(username, password))
    assert error.value.code == "invalid_credentials"
    assert str(error.value) == "用户名或密码错误"


def test_unknown_user_performs_one_dummy_verification_even_if_it_matches(
    engine: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def matching(password: str, encoded: str) -> bool:
        calls.append((password, encoded))
        return True

    monkeypatch.setattr(auth, "verify_password", matching)
    with Session(engine) as session, pytest.raises(auth.InvalidCredentialsError):
        auth.authenticate_user(session, login("不存在用户"))
    assert calls == [(PASSWORD, auth._dummy_password_hash)]


def test_legacy_user_cannot_login(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(User(external_id="legacy-user"))
        session.commit()
        with pytest.raises(auth.InvalidCredentialsError):
            auth.authenticate_user(session, login("legacy-user".replace("-", "_")))
        legacy = session.scalar(select(User).where(User.external_id == "legacy-user"))
        assert legacy is not None
        assert legacy.username is None and legacy.password_hash is None


@pytest.mark.parametrize("username,password_hash", [(None, None), ("中文用户", None)])
def test_missing_credentials_are_rejected_if_repository_returns_them(
    engine: Engine, monkeypatch: pytest.MonkeyPatch,
    username: str | None, password_hash: str | None,
) -> None:
    user = User(external_id="legacy", username=username, password_hash=password_hash)
    monkeypatch.setattr(auth, "get_user_by_username", lambda session, name: user)
    with Session(engine) as session, pytest.raises(auth.InvalidCredentialsError):
        auth.authenticate_user(session, login())


def test_corrupt_hash_remains_a_system_error(engine: Engine, user_id: int) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        assert user is not None
        user.password_hash = "broken-hash"
        session.commit()
        with pytest.raises(InvalidHashError):
            auth.authenticate_user(session, login())


def test_verification_fault_propagates(engine: Engine, user_id: int, monkeypatch: pytest.MonkeyPatch):
    failure = VerificationError("synthetic verification failure")

    def fail(password: str, encoded: str) -> NoReturn:
        raise failure

    monkeypatch.setattr(auth, "verify_password", fail)
    with Session(engine) as session, pytest.raises(VerificationError) as error:
        auth.authenticate_user(session, login())
    assert error.value is failure


def test_database_fault_propagates(engine: Engine, monkeypatch: pytest.MonkeyPatch):
    failure = OperationalError("SELECT", None, RuntimeError("synthetic database failure"))

    def fail(session: Session, username: str) -> NoReturn:
        raise failure

    monkeypatch.setattr(auth, "get_user_by_username", fail)
    with Session(engine) as session, pytest.raises(OperationalError) as error:
        auth.authenticate_user(session, login())
    assert error.value is failure


@pytest.mark.parametrize("password", [PASSWORD, "wrong"])
def test_authentication_does_not_flush_commit_or_rollback_caller_work(
    engine: Engine, user_id: int, password: str,
) -> None:
    with Session(engine, autoflush=True) as session:
        user = session.get(User, user_id)
        assert user is not None
        original_hash = user.password_hash
        pending = User(external_id="pending-caller-work")
        session.add(pending)
        transaction = session.get_transaction()
        if password == PASSWORD:
            auth.authenticate_user(session, login(password=password))
        else:
            with pytest.raises(auth.InvalidCredentialsError):
                auth.authenticate_user(session, login(password=password))
        assert session.get_transaction() is transaction
        assert pending in session.new and pending.id is None
        assert user.password_hash == original_hash
        with Session(engine) as reader:
            assert reader.scalar(select(User).where(User.external_id == pending.external_id)) is None


def test_stored_password_whitespace_is_verified_verbatim(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(User(
            external_id="imported", username="导入用户", password_hash=hash_password(" secret "),
        ))
        session.commit()
        assert auth.authenticate_user(session, login("导入用户", " secret ")).username == "导入用户"
        with pytest.raises(auth.InvalidCredentialsError):
            auth.authenticate_user(session, login("导入用户", "secret"))


def test_chinese_http_registration_to_credential_verification(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(registration, "SessionLocal", sessionmaker(bind=engine))
    client = TestClient(app)  # Do not enter the development database lifespan.
    try:
        response = client.post("/auth/register", json={
            "username": " 中文Agent ", "password": PASSWORD,
        })
        assert response.status_code == 201
        assert response.json()["username"] == "中文agent"
        duplicate = client.post("/auth/register", json={
            "username": "中文AGENT", "password": PASSWORD,
        })
        assert duplicate.status_code == 409
        with Session(engine) as session:
            identity = auth.authenticate_user(session, login(" 中文AGENT "))
            assert identity.external_id == response.json()["external_id"]
        assert PASSWORD not in caplog.text
        assert "$argon2" not in caplog.text
    finally:
        client.close()
