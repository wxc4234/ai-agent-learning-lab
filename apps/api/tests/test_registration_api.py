"""HTTP registration contracts against the isolated PostgreSQL test database.

Run from apps/api: python -m pytest tests/test_registration_api.py -q
The real app is used without entering its development-database lifespan.
"""

from collections.abc import Iterator
import asyncio
from typing import NoReturn
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException

from app.main import app
from app.models import User
from app.repositories import user_repository
from app.repositories.user_repository import get_user_by_username
from app.routers import registration
from app.schemas import RegisterRequest, RegistrationErrorResponse
from app.services.password_service import verify_password
from app.services.registration_service import RegistrationResult

PASSWORD = "Api-Test-Password-2026!"


class TrackedSession(Session):
    was_closed = False

    def close(self) -> None:
        super().close()
        self.was_closed = True


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(
        bind=engine, class_=TrackedSession, autoflush=False, expire_on_commit=False,
    )
    sessions: list[TrackedSession] = []

    def create_session() -> TrackedSession:
        session = factory()
        sessions.append(session)
        return session

    monkeypatch.setattr(registration, "SessionLocal", create_session)
    http = TestClient(app)
    try:
        yield http
    finally:
        http.close()
        assert all(session.was_closed for session in sessions)


def payload(username: str = " Api_User ") -> dict[str, str]:
    return {"username": username, "password": PASSWORD}


def assert_no_secrets(output: str) -> None:
    # Boolean intermediates avoid printing synthetic sensitive content on failure.
    for marker in (PASSWORD, "$argon2id$", "INSERT INTO", "private-db-detail"):
        leaked = marker in output
        assert not leaked, "Sensitive data appeared in HTTP response or logs"


def test_success_commits_safe_identity_and_closes_session(client: TestClient, engine: Engine):
    response = client.post("/auth/register", json=payload())
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"external_id", "username"}
    assert body["username"] == "api_user"
    assert UUID(body["external_id"]).version == 4
    assert "set-cookie" not in response.headers
    assert_no_secrets(response.text)
    with Session(engine) as session:
        user = get_user_by_username(session, "api_user")
        assert user is not None and user.password_hash is not None
        assert user.external_id == body["external_id"]
        assert verify_password(PASSWORD, user.password_hash)


def test_duplicate_returns_object_and_preserves_original(client: TestClient, engine: Engine):
    first = client.post("/auth/register", json=payload())
    with Session(engine) as session:
        user = get_user_by_username(session, "api_user")
        assert user is not None
        original_hash = user.password_hash
    response = client.post("/auth/register", json=payload("API_USER"))
    assert response.status_code == 409
    assert response.json() == {
        "code": "username_already_exists", "message": "用户名已被使用",
    }
    with Session(engine) as session:
        user = get_user_by_username(session, "api_user")
        assert user is not None
        assert user.external_id == first.json()["external_id"]
        assert user.password_hash == original_hash
        assert session.scalar(select(func.count()).select_from(User)) == 1
    assert client.post("/auth/register", json=payload("next_user")).status_code == 201


@pytest.mark.parametrize("invalid", [
    {},
    {"username": "api_user"},
    {"password": PASSWORD},
    {"username": 123, "password": PASSWORD},
    {"username": "api_user", "password": 12345678},
    {"username": "ab", "password": PASSWORD},
    {"username": "api_user", "password": "short"},
    {"username": "api_user", "password": PASSWORD + " "},
    {"username": "api_user", "password": PASSWORD, "is_admin": True},
    {"username": "api_user", "password": PASSWORD, PASSWORD: "extra"},
    [],
])
def test_invalid_input_is_redacted_without_calling_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, invalid: object,
):
    calls = []

    def unexpected_call(session: Session, request: RegisterRequest) -> NoReturn:
        calls.append(True)
        raise AssertionError("Service must not run for invalid input")

    monkeypatch.setattr(registration, "register_user", unexpected_call)
    response = client.post("/auth/register", json=invalid)
    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_registration_input",
        "message": "注册信息不符合要求，请检查用户名和密码",
    }
    assert not calls
    assert_no_secrets(response.text)


@pytest.mark.parametrize("body", [b"", b'{"username":', b"\xff"])
def test_malformed_body_is_safe(client: TestClient, body: bytes):
    response = client.post(
        "/auth/register", content=body, headers={"Content-Type": "application/json"},
    )
    assert response.status_code == (400 if body == b"\xff" else 422)
    data = response.json()
    assert isinstance(data, dict)
    assert set(data) == {"code", "message"}
    assert_no_secrets(response.text)


def test_internal_failure_response_and_logs_are_safe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    def fail(session: Session, request: RegisterRequest) -> NoReturn:
        raise RuntimeError("private-db-detail " + PASSWORD)

    monkeypatch.setattr(registration, "register_user", fail)
    response = client.post("/auth/register", json=payload())
    assert response.status_code == 500
    assert_no_secrets(response.text)
    assert_no_secrets(caplog.text)
    body = response.json()
    assert isinstance(body, dict)
    assert body["code"] == "registration_failed"
    records = [r for r in caplog.records if r.name == registration.__name__]
    assert len(records) == 1
    assert records[0].getMessage() == "registration_failed"
    assert records[0].exc_info is None


def test_other_unique_constraint_is_500_and_later_registration_succeeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, engine: Engine,
):
    first = client.post("/auth/register", json=payload())
    existing_id = UUID(first.json()["external_id"])
    with monkeypatch.context() as patch:
        patch.setattr(user_repository, "uuid4", lambda: existing_id)
        response = client.post("/auth/register", json=payload("another_user"))
    assert response.status_code == 500
    body = response.json()
    assert isinstance(body, dict)
    assert body["code"] == "registration_failed"
    assert_no_secrets(response.text)
    with Session(engine) as session:
        assert get_user_by_username(session, "another_user") is None
    assert client.post("/auth/register", json=payload("another_user")).status_code == 201


def test_http_exception_detail_is_not_exposed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    def reject(session: Session, request: RegisterRequest) -> NoReturn:
        raise HTTPException(400, detail=PASSWORD)

    monkeypatch.setattr(registration, "register_user", reject)
    response = client.post("/auth/register", json=payload())
    assert response.status_code == 400
    assert response.json() == {"code": "request_rejected", "message": "请求无法处理"}
    assert_no_secrets(response.text)


def test_sync_service_uses_worker_thread(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    original = registration.register_user
    calls: list[bool] = []

    def record(session: Session, request: RegisterRequest) -> RegistrationResult:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        calls.append(True)
        return original(session, request)

    monkeypatch.setattr(registration, "register_user", record)
    response = client.post("/auth/register", json=payload())
    assert response.status_code == 201
    assert calls == [True]


def test_openapi_and_other_route_validation_remain_intact(client: TestClient):
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/auth/register"]["post"]["responses"]
    for code in ("400", "409", "422", "500"):
        assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/RegistrationErrorResponse"
        )
    assert RegistrationErrorResponse.model_fields.keys() == {"code", "message"}
    response = client.post("/chat", json={})
    assert response.status_code == 403
    assert response.json()["code"] == "chat_origin_rejected"
