"""Logout HTTP contracts using isolated PostgreSQL; no development lifespan.

Run from apps/api: python -m pytest -q tests/auth/test_logout_api.py
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
from http.cookies import SimpleCookie
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app import dependencies
from app.config import LOGIN_COOKIE_NAME
from app.main import app
from app.models import LoginSession
from app.routers.auth import login
from app.routers.auth import logout
from app.schemas import RegisterRequest
from app.services.auth.registration_service import register_user


PASSWORD = "Logout-HTTP-2026!"
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
            {"username": "中文Agent", "password": PASSWORD},
        ))
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(login, "SessionLocal", factory)
    monkeypatch.setattr(dependencies, "SessionLocal", factory)
    monkeypatch.setattr(logout.settings, "login_allowed_origins", (ORIGIN,))
    monkeypatch.setattr(logout.settings, "login_cookie_secure", True)
    sessions: list[TrackedSession] = []

    def create_session() -> TrackedSession:
        session = TrackedSession(engine)
        sessions.append(session)
        return session

    monkeypatch.setattr(logout, "SessionLocal", create_session)
    http = TestClient(app, base_url="https://testserver")
    try:
        yield http
    finally:
        http.close()
        assert all(session.was_closed and not session.in_transaction() for session in sessions)


def sign_in(client: TestClient) -> str:
    response = client.post("/auth/login", json={
        "username": " 中文AGENT ", "password": PASSWORD,
    }, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    token = client.cookies.get(LOGIN_COOKIE_NAME)
    assert token is not None
    return token


def assert_deleted(response, secure: bool = True) -> None:
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert set(cookies) == {LOGIN_COOKIE_NAME}
    cookie = cookies[LOGIN_COOKIE_NAME]
    assert cookie.value == "" and cookie["max-age"] == "0"
    assert parsedate_to_datetime(cookie["expires"]) <= datetime.now(UTC)
    assert cookie["path"] == "/" and cookie["domain"] == ""
    assert cookie["httponly"] and cookie["samesite"].lower() == "lax"
    assert bool(cookie["secure"]) is secure


def stored_revocation(engine: Engine, token: str) -> datetime | None:
    with Session(engine) as reader:
        row = reader.scalar(select(LoginSession).where(
            LoginSession.token_hash == sha256(token.encode()).hexdigest(),
        ))
        assert row is not None
        return row.revoked_at


@pytest.mark.parametrize("secure", [True, False])
def test_login_me_logout_and_old_token_replay(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, secure: bool,
):
    monkeypatch.setattr(logout.settings, "login_cookie_secure", secure)
    token = sign_in(client)
    assert client.get("/auth/me").status_code == 200
    response = client.post("/auth/logout", headers={"Origin": ORIGIN})
    assert_deleted(response, secure)
    assert client.cookies.get(LOGIN_COOKIE_NAME) is None
    assert stored_revocation(engine, token) is not None
    assert client.get("/auth/me").status_code == 401
    replay = client.get("/auth/me", headers={"Cookie": f"{LOGIN_COOKIE_NAME}={token}"})
    assert replay.status_code == 401
    assert token not in replay.text


@pytest.mark.parametrize("cookie", [None, "", "short", "A" * 43])
def test_missing_invalid_unknown_cookie_is_idempotent(client: TestClient, cookie: str | None):
    headers = {"Origin": ORIGIN}
    if cookie is not None:
        headers["Cookie"] = f"{LOGIN_COOKIE_NAME}={cookie}"
    assert_deleted(client.post("/auth/logout", headers=headers))


def test_repeated_old_cookie_preserves_timestamp(client: TestClient, engine: Engine):
    token = sign_in(client)
    headers = {"Origin": ORIGIN, "Cookie": f"{LOGIN_COOKIE_NAME}={token}"}
    assert_deleted(client.post("/auth/logout", headers=headers))
    first = stored_revocation(engine, token)
    assert first is not None
    assert_deleted(client.post("/auth/logout", headers=headers))
    assert stored_revocation(engine, token) == first


def test_expired_record_is_revoked(client: TestClient, engine: Engine):
    token = sign_in(client)
    with Session(engine) as writer:
        row = writer.scalar(select(LoginSession))
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(hours=2)
        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        writer.commit()
    assert_deleted(client.post("/auth/logout", headers={"Origin": ORIGIN}))
    assert stored_revocation(engine, token) is not None


def test_other_login_stays_valid(client: TestClient):
    other = sign_in(client)
    current = sign_in(client)
    assert other != current
    assert_deleted(client.post("/auth/logout", headers={"Origin": ORIGIN}))
    response = client.get("/auth/me", headers={"Cookie": f"{LOGIN_COOKIE_NAME}={other}"})
    assert response.status_code == 200


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example",
    "http://localhost:3000.evil.example", "https://localhost:3000", "http://localhost:3001"])
def test_origin_rejection_does_not_revoke_or_clear(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, origin: str | None,
):
    token = sign_in(client)

    def forbidden() -> NoReturn:
        pytest.fail("Rejected origin must not create a Session")

    monkeypatch.setattr(logout, "SessionLocal", forbidden)
    response = client.post("/auth/logout", headers={} if origin is None else {"Origin": origin})
    assert response.status_code == 403
    assert response.json() == {"code": "logout_origin_rejected", "message": "登出请求来源不被允许"}
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert client.cookies.get(LOGIN_COOKIE_NAME) == token
    assert stored_revocation(engine, token) is None
    assert client.get("/auth/me").status_code == 200


def test_commit_failure_is_safe_and_retry_succeeds(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    token = sign_in(client)

    def fail_commit(session: TrackedSession) -> NoReturn:
        row = session.scalar(select(LoginSession))
        assert row is not None and row.revoked_at is not None
        raise OperationalError("COMMIT", None, RuntimeError("private-db-detail " + token + PASSWORD))

    with monkeypatch.context() as patch:
        patch.setattr(TrackedSession, "commit", fail_commit)
        response = client.post("/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json() == {"code": "logout_failed", "message": "登出失败，请稍候再试"}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert client.cookies.get(LOGIN_COOKIE_NAME) == token
    assert stored_revocation(engine, token) is None
    for output in (response.text, caplog.text):
        assert token not in output and PASSWORD not in output and "private-db-detail" not in output
    records = [record for record in caplog.records if record.name == logout.__name__]
    assert len(records) == 1 and records[0].getMessage() == "logout_failed"
    assert records[0].exc_info is None
    assert client.get("/auth/me").status_code == 200
    assert_deleted(client.post("/auth/logout", headers={"Origin": ORIGIN}))


def test_sync_worker_secret_wrapping_and_distinct_sessions(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    sign_in(client)
    original = logout.logout_user
    sessions: list[Session] = []
    tokens: list[SecretStr | None] = []

    def record(session: Session, token: SecretStr | None) -> bool:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        assert not session.in_transaction()
        sessions.append(session)
        tokens.append(token)
        return original(session, token)

    monkeypatch.setattr(logout, "logout_user", record)
    assert_deleted(client.post("/auth/logout", headers={"Origin": ORIGIN}))
    assert_deleted(client.post("/auth/logout", headers={"Origin": ORIGIN}))
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert isinstance(tokens[0], SecretStr) and tokens[1] is None


def test_no_body_contract_and_openapi(client: TestClient):
    # The endpoint does not parse a body or require Content-Type.
    assert_deleted(client.post("/auth/logout", content=b"not-json", headers={
        "Origin": ORIGIN, "Content-Type": "text/plain",
    }))
    operation = client.get("/openapi.json").json()["paths"]["/auth/logout"]["post"]
    assert "requestBody" not in operation
    assert "content" not in operation["responses"]["204"]
    for code in ("403", "500"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/LogoutErrorResponse",
        )
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/chat", json={}).status_code == 403
