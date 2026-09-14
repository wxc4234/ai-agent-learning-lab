"""Authentication dependency lifecycle on the shared isolated PostgreSQL fixture."""

import asyncio
from dataclasses import FrozenInstanceError
from threading import get_ident

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import dependencies
from app.config import LOGIN_COOKIE_NAME
from app.dependencies import CurrentUser
from app.routers.current_user import CurrentUserRoute
from app.schemas import LoginRequest, RegisterRequest
from app.services.login_session_service import issue_login_session
from app.services.registration_service import register_user

PASSWORD = "Dependency-Test-2026!"


@pytest.fixture
def identities(engine: Engine):
    results = []
    for username in ("用户甲", "用户乙"):
        with Session(engine) as session:
            register_user(session, RegisterRequest(username=username, password=PASSWORD))
        with Session(engine) as session:
            results.append(issue_login_session(
                session, LoginRequest(username=username, password=PASSWORD),
            ))
    return results


@pytest.fixture
def probe(engine: Engine, monkeypatch: pytest.MonkeyPatch):
    sessions = []
    entered = []
    workers = []

    class TrackedSession(Session):
        closed_for_test = False

        def close(self):
            super().close()
            self.closed_for_test = True
            assert not self.in_transaction()

    def factory():
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        workers.append(get_ident())
        session = TrackedSession(engine)
        sessions.append(session)
        return session

    monkeypatch.setattr(dependencies, "SessionLocal", factory)
    app = FastAPI()
    router = APIRouter(route_class=CurrentUserRoute)

    def ensure_closed():
        assert sessions
        assert all(session.closed_for_test for session in sessions)

    @router.get("/probe")
    async def inspect_identity(identity: CurrentUser, same_identity: CurrentUser):
        ensure_closed()
        assert asyncio.get_running_loop().is_running()
        assert get_ident() not in workers
        assert identity is same_identity
        with pytest.raises(FrozenInstanceError):
            identity.username = "changed"
        entered.append(identity)
        return {"external_id": identity.external_id, "username": identity.username}

    @router.get("/stream")
    async def stream(identity: CurrentUser):
        ensure_closed()
        entered.append(identity)

        async def body():
            ensure_closed()
            yield "test stream"

        return StreamingResponse(body(), media_type="text/plain")

    app.include_router(router)
    with TestClient(app) as client:
        yield client, sessions, entered
    assert all(session.closed_for_test for session in sessions)


def cookie(result):
    return {"Cookie": f"{LOGIN_COOKIE_NAME}={result.token.get_secret_value()}"}


def test_identity_is_cached_only_within_request_and_cannot_be_forged(probe, identities):
    client, sessions, entered = probe
    for result in (identities[0], identities[1], identities[0]):
        response = client.get("/probe?external_id=forged&username=forged", headers=cookie(result))
        assert response.status_code == 200
        assert response.json() == {
            "external_id": result.user.external_id, "username": result.user.username,
        }
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
    assert len(sessions) == 3
    assert len({id(session) for session in sessions}) == 3
    assert entered[0] is not entered[2]
    assert entered[0] == entered[2]


def test_session_is_closed_before_stream_body(probe, identities):
    client, sessions, entered = probe
    response = client.get("/stream", headers=cookie(identities[0]))
    assert response.status_code == 200
    assert response.text == "test stream"
    assert len(sessions) == len(entered) == 1


@pytest.mark.parametrize("token", [None, "", "short", "x" * 43])
def test_failed_authentication_never_executes_endpoint(probe, token):
    client, sessions, entered = probe
    headers = {} if token is None else {"Cookie": f"{LOGIN_COOKIE_NAME}={token}"}
    response = client.get("/probe", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_login_session"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert entered == []
    assert len(sessions) == 1


def test_real_sql_failure_closes_session_and_skips_endpoint(probe, identities, monkeypatch, caplog):
    client, sessions, entered = probe

    def fail(session, token):
        assert isinstance(token, SecretStr)
        session.execute(text("SELECT * FROM dependency_missing_test_table"))

    monkeypatch.setattr(dependencies, "resolve_login_session", fail)
    response = client.get("/probe", headers=cookie(identities[0]))
    assert response.status_code == 500
    assert response.json()["code"] == "current_user_failed"
    assert entered == []
    assert len(sessions) == 1 and sessions[0].closed_for_test
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    for output in (response.text, caplog.text):
        assert "dependency_missing_test_table" not in output
        assert identities[0].token.get_secret_value() not in output


def test_factory_failure_is_safe_and_skips_endpoint(probe, monkeypatch, caplog):
    client, sessions, entered = probe

    def fail():
        raise RuntimeError(PASSWORD)

    monkeypatch.setattr(dependencies, "SessionLocal", fail)
    response = client.get("/probe")
    assert response.status_code == 500
    assert PASSWORD not in response.text + caplog.text
    assert entered == sessions == []


def test_openapi_does_not_accept_identity_as_client_input(probe):
    client, _, _ = probe
    operation = client.get("/openapi.json").json()["paths"]["/probe"]["get"]
    assert "requestBody" not in operation
    assert not operation.get("parameters")
