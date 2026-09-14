"""Login HTTP contracts; PostgreSQL fixtures never enter app lifespan.

Run from apps/api: python -m pytest -q tests/test_login_api.py
"""

import asyncio
from collections.abc import Iterator
from email.utils import parsedate_to_datetime
from hashlib import sha256
from http.cookies import SimpleCookie
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import LoginSession
from app.routers import login
from app.schemas import LoginRequest, RegisterRequest
from app.services.login_session_service import LoginSessionResult
from app.services.registration_service import register_user


PASSWORD = "HTTP-Login-2026!"
ORIGIN = "http://localhost:3000"


class TrackedSession(Session):
    was_closed = False

    def close(self) -> None:
        super().close()
        self.was_closed = True


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with Session(engine) as session:
        register_user(session, RegisterRequest.model_validate(
            {"username": "中文agent", "password": PASSWORD},
        ))
    factory = sessionmaker(bind=engine, class_=TrackedSession, autoflush=False)
    sessions: list[TrackedSession] = []

    def create_session() -> TrackedSession:
        session = factory()
        sessions.append(session)
        return session

    monkeypatch.setattr(login, "SessionLocal", create_session)
    monkeypatch.setattr(login.settings, "login_allowed_origins", (ORIGIN,))
    monkeypatch.setattr(login.settings, "login_cookie_secure", True)
    http = TestClient(app, base_url="https://testserver")
    try:
        yield http
    finally:
        http.close()
        assert all(session.was_closed for session in sessions)


def payload(username: str = " 中文AGENT ", password: str = PASSWORD) -> dict[str, str]:
    return {"username": username, "password": password}


def count_sessions(engine: Engine) -> int:
    with Session(engine) as reader:
        return reader.scalar(select(func.count()).select_from(LoginSession)) or 0


def assert_error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    for secret in (PASSWORD, "$argon2", "private-db-detail"):
        leaked = secret in response.text
        assert not leaked, "Sensitive data in error response"


@pytest.mark.parametrize("secure", [True, False])
def test_success_cookie_matches_committed_record(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, secure: bool,
) -> None:
    monkeypatch.setattr(login.settings, "login_cookie_secure", secure)
    response = client.post("/auth/login", json=payload(), headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert set(response.json()) == {"external_id", "username"}
    assert response.json()["username"] == "中文agent"
    assert response.headers["cache-control"] == "no-store"
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert set(cookies) == {"agent_session"}
    cookie = cookies["agent_session"]
    assert cookie["httponly"]
    assert bool(cookie["secure"]) is secure
    assert cookie["samesite"].lower() == "lax"
    assert cookie["path"] == "/" and cookie["domain"] == ""
    token = cookie.value
    assert token not in response.text and token not in caplog.text
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    with Session(engine) as reader:
        stored = reader.scalar(select(LoginSession))
        assert stored is not None
        assert stored.token_hash == sha256(token.encode()).hexdigest()
        assert parsedate_to_datetime(cookie["expires"]) == stored.expires_at.replace(
            microsecond=0,
        )
        assert token not in [getattr(stored, col.key) for col in LoginSession.__table__.columns]


@pytest.mark.parametrize("username,password", [
    ("不存在用户", PASSWORD), ("中文agent", "wrong"), ("中文agent", PASSWORD + " "),
])
def test_invalid_credentials_no_cookie_or_write(
    client: TestClient, engine: Engine, username: str, password: str,
) -> None:
    response = client.post(
        "/auth/login", json=payload(username, password), headers={"Origin": ORIGIN},
    )
    assert_error(response, 401, "invalid_credentials")
    assert count_sessions(engine) == 0


@pytest.mark.parametrize("origin", [
    None, "null", "https://evil.example", "http://localhost:3000.evil.example",
    "http://localhost:3001", "https://localhost:3000", "http://localhost:3000/",
])
def test_origin_rejection_precedes_service(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, origin: str | None,
) -> None:
    def forbidden() -> NoReturn:
        pytest.fail("Rejected origin must not create a Session")

    monkeypatch.setattr(login, "SessionLocal", forbidden)
    headers = {} if origin is None else {"Origin": origin}
    response = client.post("/auth/login", json=payload(), headers=headers)
    assert_error(response, 403, "login_origin_rejected")
    assert count_sessions(engine) == 0


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_content_type_rejection(client: TestClient, engine: Engine, content_type: str | None):
    headers = {"Origin": ORIGIN}
    if content_type is not None:
        headers["Content-Type"] = content_type
    response = client.post("/auth/login", content=b"{}", headers=headers)
    assert_error(response, 415, "unsupported_login_content_type")
    assert count_sessions(engine) == 0


def test_json_charset_is_supported(client: TestClient):
    response = client.post("/auth/login", json=payload(), headers={
        "Origin": ORIGIN, "Content-Type": "application/json; charset=utf-8",
    })
    assert response.status_code == 200


@pytest.mark.parametrize("body", [
    {}, [], {"username": "中文agent"}, {"username": "ab", "password": PASSWORD},
    {"username": "中文agent", "password": 123},
    {"username": "中文agent", "password": PASSWORD, PASSWORD: "extra"},
])
def test_input_error_is_422_and_redacted(client: TestClient, engine: Engine, body: object):
    response = client.post("/auth/login", json=body, headers={"Origin": ORIGIN})
    assert_error(response, 422, "invalid_login_input")
    assert count_sessions(engine) == 0


@pytest.mark.parametrize("body,status_code,code", [
    (b"", 422, "invalid_login_input"),
    (b'{"username":', 422, "invalid_login_input"),
    (b"\xff", 400, "request_rejected"),
])
def test_malformed_body(client: TestClient, body: bytes, status_code: int, code: str):
    response = client.post("/auth/login", content=body, headers={
        "Origin": ORIGIN, "Content-Type": "application/json",
    })
    assert_error(response, status_code, code)


def test_internal_error_is_safe(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(session: Session, request: LoginRequest) -> NoReturn:
        raise RuntimeError("private-db-detail " + PASSWORD)

    monkeypatch.setattr(login, "issue_login_session", fail)
    response = client.post("/auth/login", json=payload(), headers={"Origin": ORIGIN})
    assert_error(response, 500, "login_failed")
    assert count_sessions(engine) == 0
    assert PASSWORD not in caplog.text and "private-db-detail" not in caplog.text
    records = [record for record in caplog.records if record.name == login.__name__]
    assert len(records) == 1
    assert records[0].getMessage() == "login_failed" and records[0].exc_info is None


def test_commit_failure_has_no_cookie_and_next_request_recovers(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_commit(session: TrackedSession) -> NoReturn:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 1
        raise OperationalError("COMMIT", None, RuntimeError("private-db-detail"))

    with monkeypatch.context() as patch:
        patch.setattr(TrackedSession, "commit", fail_commit)
        response = client.post("/auth/login", json=payload(), headers={"Origin": ORIGIN})
    assert_error(response, 500, "login_failed")
    assert count_sessions(engine) == 0
    assert client.post("/auth/login", json=payload(), headers={"Origin": ORIGIN}).status_code == 200


def test_each_request_uses_fresh_worker_thread_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = login.issue_login_session
    sessions: list[Session] = []

    def record(session: Session, request: LoginRequest) -> LoginSessionResult:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        assert not session.in_transaction()
        sessions.append(session)
        return original(session, request)

    monkeypatch.setattr(login, "issue_login_session", record)
    for _ in range(2):
        assert client.post("/auth/login", json=payload(), headers={"Origin": ORIGIN}).status_code == 200
    assert len(sessions) == 2 and sessions[0] is not sessions[1]


def test_openapi_and_chat_validation(client: TestClient):
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/auth/login"]["post"]["responses"]
    for code in ("400", "401", "403", "415", "422", "500"):
        assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/LoginErrorResponse",
        )
    response = client.post("/chat", json={})
    assert response.status_code == 403
    assert response.json()["code"] == "chat_origin_rejected"
