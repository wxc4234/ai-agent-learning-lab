"""Cookie login -> current-user contracts on isolated PostgreSQL.

Run from apps/api: python -m pytest -q tests/test_current_user_api.py
No TestClient lifespan: never initialize development tables.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import dependencies
from app.config import LOGIN_COOKIE_NAME
from app.main import app
from app.models import LoginSession
from app.repositories.login_session_repository import revoke_login_session
from app.routers import current_user, login
from app.schemas import RegisterRequest
from app.services import login_session_resolver
from app.services.authentication_service import AuthenticatedUser
from app.services.registration_service import register_user


PASSWORD = "Current-User-2026!"
ORIGIN = "http://localhost:3000"


class ReadSession(Session):
    was_closed = False

    def commit(self) -> NoReturn:
        pytest.fail("Current-user GET must not commit")

    def flush(self, objects=None) -> NoReturn:
        pytest.fail("Current-user GET must not flush")

    def close(self) -> None:
        super().close()
        self.was_closed = True


@pytest.fixture
def read_sessions() -> list[ReadSession]:
    return []


@pytest.fixture
def client(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, read_sessions: list[ReadSession],
) -> Iterator[TestClient]:
    with Session(engine) as session:
        register_user(session, RegisterRequest.model_validate(
            {"username": "中文Agent", "password": PASSWORD},
        ))
    monkeypatch.setattr(login, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(login.settings, "login_allowed_origins", (ORIGIN,))
    monkeypatch.setattr(login.settings, "login_cookie_secure", True)

    def factory() -> ReadSession:
        session = ReadSession(engine, autoflush=True)
        read_sessions.append(session)
        return session

    monkeypatch.setattr(dependencies, "SessionLocal", factory)
    http = TestClient(app, base_url="https://testserver")
    try:
        yield http
    finally:
        http.close()
        assert all(session.was_closed and not session.in_transaction() for session in read_sessions)


def sign_in(client: TestClient) -> Response:
    response = client.post("/auth/login", json={
        "username": " 中文AGENT ", "password": PASSWORD,
    }, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert client.cookies.get(LOGIN_COOKIE_NAME)
    return response


def assert_common(response: Response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert PASSWORD not in response.text and "$argon2" not in response.text


def assert_unauthorized(response: Response) -> None:
    assert response.status_code == 401
    assert response.json() == {
        "code": "invalid_login_session", "message": "登录状态无效，请重新登录",
    }
    assert_common(response)


def test_login_cookie_returns_safe_chinese_identity(
    client: TestClient, caplog: pytest.LogCaptureFixture, read_sessions: list[ReadSession],
) -> None:
    signed = sign_in(client)
    token = client.cookies.get(LOGIN_COOKIE_NAME)
    assert token is not None
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json() == signed.json()
    assert set(response.json()) == {"external_id", "username"}
    assert response.json()["username"] == "中文agent"
    assert_common(response)
    assert token not in response.text and token not in caplog.text
    assert len(read_sessions) == 1 and read_sessions[0].was_closed
    assert client.cookies.get(LOGIN_COOKIE_NAME) == token


@pytest.mark.parametrize("cookie", [None, "", "short", "A" * 42, "A" * 43, "A" * 44])
def test_missing_or_invalid_cookie_is_401(client: TestClient, cookie: str | None):
    headers = {} if cookie is None else {"Cookie": f"{LOGIN_COOKIE_NAME}={cookie}"}
    response = client.get("/auth/me", headers=headers)
    assert_unauthorized(response)
    if cookie:
        assert cookie not in response.text


@pytest.mark.parametrize("state", ["expired", "revoked", "future"])
def test_persisted_state_rejects_cookie_still_held_by_client(
    client: TestClient, engine: Engine, state: str,
) -> None:
    sign_in(client)
    token = client.cookies.get(LOGIN_COOKIE_NAME)
    assert token is not None
    digest = sha256(token.encode()).hexdigest()
    now = datetime.now(UTC)
    with Session(engine) as writer:
        stored = writer.scalar(select(LoginSession).where(LoginSession.token_hash == digest))
        assert stored is not None
        if state == "revoked":
            assert revoke_login_session(writer, token_hash=digest, now=now)
        elif state == "expired":
            stored.created_at = now - timedelta(hours=2)
            stored.expires_at = now - timedelta(hours=1)
        else:
            stored.created_at = now + timedelta(hours=1)
            stored.expires_at = now + timedelta(hours=2)
        writer.commit()
    assert client.cookies.get(LOGIN_COOKIE_NAME) == token
    assert_unauthorized(client.get("/auth/me"))
    assert client.cookies.get(LOGIN_COOKIE_NAME) == token


def test_client_identity_fields_cannot_impersonate_another_user(client: TestClient, engine: Engine):
    signed = sign_in(client)
    with Session(engine) as writer:
        other = register_user(writer, RegisterRequest.model_validate(
            {"username": "another_user", "password": PASSWORD},
        ))
    response = client.request("GET", "/auth/me", params={"external_id": other.external_id},
        headers={"X-User-Id": other.external_id}, json={"external_id": other.external_id})
    assert response.status_code == 200 and response.json() == signed.json()
    assert_common(response)


def test_get_does_not_require_login_post_headers(client: TestClient):
    sign_in(client)
    response = client.get("/auth/me", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 200
    assert_common(response)
    # No CORS grants are introduced; this test is an HTTP contract, not a browser SOP test.
    assert "access-control-allow-origin" not in response.headers


def test_query_failure_is_safe_and_connection_recovers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    sign_in(client)
    token = client.cookies.get(LOGIN_COOKIE_NAME)
    assert token is not None

    def fail_query(session: Session, *args, **kwargs):
        return session.execute(text("SELECT * FROM current_user_missing_test_table"))

    with monkeypatch.context() as patch:
        patch.setattr(login_session_resolver, "get_active_login_session", fail_query)
        response = client.get("/auth/me")
    assert response.status_code == 500
    assert response.json() == {
        "code": "current_user_failed", "message": "获取当前用户失败，请稍候再试",
    }
    assert_common(response)
    for output in (response.text, caplog.text):
        assert token not in output and PASSWORD not in output
        assert "current_user_missing_test_table" not in output
    records = [record for record in caplog.records if record.name == current_user.__name__]
    assert len(records) == 1 and records[0].getMessage() == "current_user_failed"
    assert records[0].exc_info is None
    assert client.get("/auth/me").status_code == 200


def test_response_validation_failure_is_safe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def invalid_identity(session: Session, token: SecretStr | None):
        return AuthenticatedUser(id=1, external_id=PASSWORD, username=None)  # type: ignore[arg-type]

    monkeypatch.setattr(dependencies, "resolve_login_session", invalid_identity)
    response = client.get("/auth/me")
    assert response.status_code == 500
    assert_common(response)
    assert PASSWORD not in caplog.text


def test_secret_wrapping_worker_thread_and_distinct_sessions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, read_sessions: list[ReadSession],
) -> None:
    sign_in(client)
    original = dependencies.resolve_login_session
    tokens: list[SecretStr | None] = []

    def record(session: Session, token: SecretStr | None) -> AuthenticatedUser:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        assert not session.in_transaction()
        tokens.append(token)
        return original(session, token)

    monkeypatch.setattr(dependencies, "resolve_login_session", record)
    assert client.get("/auth/me").status_code == 200
    client.cookies.clear()
    assert_unauthorized(client.get("/auth/me"))
    assert isinstance(tokens[0], SecretStr) and tokens[1] is None
    assert len(read_sessions) == 2 and read_sessions[0] is not read_sessions[1]


def test_openapi_and_existing_validation(client: TestClient):
    operation = client.get("/openapi.json").json()["paths"]["/auth/me"]["get"]
    assert "requestBody" not in operation
    for code in ("401", "500"):
        assert operation["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/CurrentUserErrorResponse",
        )
    response = client.post("/chat", json={})
    assert response.status_code == 403
    assert response.json()["code"] == "chat_origin_rejected"
