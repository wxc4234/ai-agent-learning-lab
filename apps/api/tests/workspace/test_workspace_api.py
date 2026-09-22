"""真实应用注册、Cookie 身份与 Workspace HTTP 边界；数据库仅使用隔离夹具。"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from app import dependencies
from app.main import app
from app.models import LoginSession, Workspace
from app.routers.workspace import projects as workspace
from app.schemas import LoginRequest, RegisterRequest
from app.services.auth.login_session_service import issue_login_session
from app.services.auth.registration_service import register_user


@pytest.fixture
def lab(engine, monkeypatch):
    identities = []
    for name in ("工作空间甲", "工作空间乙"):
        with Session(engine) as session:
            register_user(session, RegisterRequest(username=name, password="Workspace-HTTP-2026!"))
        with Session(engine) as session:
            identities.append(issue_login_session(session, LoginRequest(username=name, password="Workspace-HTTP-2026!")))
    auth_sessions = []
    business_sessions = []

    class TrackedSession(Session):
        was_closed = False

        def close(self):
            super().close()
            self.was_closed = True
            assert not self.in_transaction()

    def make_session(collection):
        # 同步依赖和普通 def 路由应在线程池执行，不能占用事件循环线程。
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        session = TrackedSession(engine)
        collection.append(session)
        return session

    def business_factory():
        # 创建业务 Session 前，认证依赖应已释放自己的连接。
        assert auth_sessions and all(session.was_closed for session in auth_sessions)
        return make_session(business_sessions)

    monkeypatch.setattr(dependencies, "SessionLocal", lambda: make_session(auth_sessions))
    monkeypatch.setattr(workspace, "SessionLocal", business_factory)
    # 使用真实 app 检查路由注册，但不进入访问开发数据库的 lifespan。
    client = TestClient(app)
    try:
        yield client, identities, auth_sessions, business_sessions, TrackedSession
    finally:
        client.close()
        assert all(session.was_closed for session in auth_sessions + business_sessions)


def headers(identity):
    return {"Origin": "http://localhost:3000", "Cookie": f"agent_session={identity.token.get_secret_value()}"}


def count(engine):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(Workspace))


def assert_safe(response, status, code=None):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    if code:
        assert set(response.json()) == {"code", "message"}
        assert response.json()["code"] == code
    assert "PRIVATE" not in response.text


def test_registered_route_creates_for_cookie_owner_and_closes_sessions(lab, engine):
    client, (owner, other), auth_sessions, business_sessions, _ = lab
    response = client.post(f"/workspaces?user_id={other.user.id}",
                           headers=headers(owner) | {"Content-Type": "application/json; charset=utf-8", "X-User-ID": str(other.user.id)},
                           json={"name": " \t甲的 Agent 项目\n"})
    assert_safe(response, 201)
    body = response.json()
    assert set(body) == {"external_id", "name", "created_at"}
    assert body["name"] == "甲的 Agent 项目"
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
    assert len(auth_sessions) == len(business_sessions) == 1
    assert auth_sessions[0] is not business_sessions[0]
    assert auth_sessions[0].was_closed and business_sessions[0].was_closed
    with Session(engine) as session:
        record = session.scalar(select(Workspace))
        assert record.user_id == owner.user.id
        assert record.external_id == body["external_id"]
    second = client.post("/workspaces", headers=headers(other), json={"name": body["name"]})
    assert_safe(second, 201)
    assert second.json()["external_id"] != body["external_id"]
    assert count(engine) == 2


@pytest.mark.parametrize("state", ["missing", "malformed", "unknown", "expired", "revoked"])
def test_invalid_auth_rejected_before_business_work(lab, engine, state):
    client, (owner, _), _, business_sessions, _ = lab
    request_headers = headers(owner)
    if state == "missing":
        request_headers.pop("Cookie")
    elif state in ("malformed", "unknown"):
        request_headers["Cookie"] = "agent_session=" + ("short" if state == "malformed" else "z" * 43)
    else:
        with Session(engine) as session, session.begin():
            record = session.scalar(select(LoginSession).where(LoginSession.user_id == owner.user.id))
            if state == "expired":
                record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
                record.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            else:
                record.revoked_at = datetime.now(timezone.utc)
    response = client.post("/workspaces", headers=request_headers, json={"name": "PRIVATE"})
    assert_safe(response, 401, "invalid_login_session")
    assert not business_sessions and count(engine) == 0


@pytest.mark.parametrize("origin", [None, "null", "http://localhost:3000.evil.test", "https://evil.test"])
def test_origin_rejected_before_auth(lab, engine, origin):
    client, (owner, _), auth_sessions, business_sessions, _ = lab
    request_headers = headers(owner)
    if origin is None:
        request_headers.pop("Origin")
    else:
        request_headers["Origin"] = origin
    response = client.post("/workspaces", headers=request_headers, json={"name": "PRIVATE"})
    assert_safe(response, 403, "workspace_origin_rejected")
    assert not auth_sessions and not business_sessions and count(engine) == 0


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_non_json_rejected_before_auth(lab, engine, content_type):
    client, (owner, _), auth_sessions, _, _ = lab
    request_headers = headers(owner)
    if content_type is not None:
        request_headers["Content-Type"] = content_type
    response = client.post("/workspaces", headers=request_headers, content='{"name":"PRIVATE"}')
    assert_safe(response, 415, "unsupported_workspace_content_type")
    assert not auth_sessions and count(engine) == 0


@pytest.mark.parametrize("payload", [{}, {"name": 123}, {"name": None}, {"name": []},
                                     {"name": "PRIVATE", "user_id": 999}, {"name": "PRIVATE", "external_id": "chosen"}])
def test_strict_payload_rejects_extra_identity_or_wrong_type(lab, engine, payload):
    client, (owner, _), _, business_sessions, _ = lab
    response = client.post("/workspaces", headers=headers(owner), json=payload)
    assert_safe(response, 422, "invalid_workspace_input")
    assert not business_sessions and count(engine) == 0


@pytest.mark.parametrize("name", ["", " \t\n\u3000", "a" * 101])
def test_invalid_name_is_business_422(lab, engine, name):
    client, (owner, _), _, _, _ = lab
    response = client.post("/workspaces", headers=headers(owner), json={"name": name})
    assert_safe(response, 422, "invalid_workspace_name")
    assert count(engine) == 0


def test_malformed_json_is_safe_422(lab, engine):
    client, (owner, _), _, business_sessions, _ = lab
    response = client.post("/workspaces", headers=headers(owner) | {"Content-Type": "application/json"}, content='{"PRIVATE":')
    assert_safe(response, 422, "invalid_workspace_input")
    assert not business_sessions and count(engine) == 0


def test_auth_database_fault_is_500_not_401(lab, engine, monkeypatch, caplog):
    client, (owner, _), _, business_sessions, _ = lab

    def fail_auth(session, token):
        session.execute(text("SELECT * FROM missing_private_auth_table"))

    monkeypatch.setattr(dependencies, "resolve_login_session", fail_auth)
    response = client.post("/workspaces", headers=headers(owner), json={"name": "PRIVATE"})
    assert_safe(response, 500, "workspace_creation_failed")
    assert "missing_private_auth_table" not in response.text + caplog.text
    assert not business_sessions and count(engine) == 0


def test_failed_commit_rolls_back_and_returns_safe_500(lab, engine, monkeypatch, caplog):
    client, (owner, _), _, _, session_class = lab

    def fail_commit(session):
        # 故障发生在真实 flush 之后，HTTP 500 不应留下未提交的工作空间。
        assert session.scalar(select(func.count()).select_from(Workspace)) == 1
        raise OperationalError("PRIVATE SQL", None, RuntimeError("PRIVATE database"))

    with monkeypatch.context() as patch:
        patch.setattr(session_class, "commit", fail_commit)
        response = client.post("/workspaces", headers=headers(owner), json={"name": "PRIVATE"})
    assert_safe(response, 500, "workspace_creation_failed")
    assert "PRIVATE" not in caplog.text
    assert count(engine) == 0
    assert client.post("/workspaces", headers=headers(owner), json={"name": "恢复"}).status_code == 201


def test_response_assembled_after_close_and_serialization_error_is_safe(lab, engine, monkeypatch, caplog):
    client, (owner, _), _, business_sessions, _ = lab

    def invalid_response(**kwargs):
        assert business_sessions and all(session.was_closed for session in business_sessions)
        return {"external_id": "PRIVATE", "name": "PRIVATE", "created_at": "invalid"}

    monkeypatch.setattr(workspace, "WorkspaceResponse", invalid_response)
    response = client.post("/workspaces", headers=headers(owner), json={"name": "项目"})
    assert_safe(response, 500, "workspace_creation_failed")
    assert "PRIVATE" not in caplog.text
    # 响应失败发生在服务提交之后；不能声称所有 500 都会撤销已提交的数据。
    assert count(engine) == 1


@pytest.mark.parametrize("status", [400, 418])
def test_http_exception_detail_is_never_forwarded(lab, engine, monkeypatch, status):
    client, (owner, _), _, _, _ = lab
    failure = Mock(side_effect=HTTPException(status_code=status, detail="PRIVATE"))
    monkeypatch.setattr(workspace, "create_user_workspace", failure)
    response = client.post("/workspaces", headers=headers(owner), json={"name": "项目"})
    assert_safe(response, 400 if status == 400 else 500)
    assert count(engine) == 0


def test_openapi_advertises_registered_contract_without_user_id():
    operation = app.openapi()["paths"]["/workspaces"]["post"]
    assert set(operation["responses"]) == {"201", "400", "401", "403", "415", "422", "500"}
    schema = app.openapi()["components"]["schemas"]["WorkspaceCreateRequest"]
    assert set(schema["properties"]) == {"name"}
    assert schema["additionalProperties"] is False
