"""审批HTTP边界：真实local身份、隔离事务及安全响应。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Conversation, FileEditProposal, User, Workspace
from app.routers.workspace import proposals as routes
from app.services.workspace.proposals import file_edit_proposal_decision as decisions
from tests.local.test_local_mode import HEADERS
from tests.workspace.proposals import test_file_edit_proposal_api as detail_tests

local_client = detail_tests.local_client
saved = detail_tests.saved
safe = detail_tests.safe


@pytest.fixture
def ready(saved, engine, monkeypatch):
    # 服务拥有独立Session，允许真实提交；根夹具负责独立数据库与schema清理。
    sessions = []

    class DecisionSession(Session):
        closed = False

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

        def close(self):
            super().close()
            self.closed = True

    monkeypatch.setattr(decisions, "SessionLocal", sessionmaker(
        bind=engine, class_=DecisionSession, expire_on_commit=False,
    ))
    yield saved, sessions, DecisionSession
    assert all(s.closed and not s.in_transaction() for s in sessions)


def stored(engine):
    with Session(engine) as session:
        return session.scalar(select(FileEditProposal.status))


def send(client, path, decision="approved", **kwargs):
    return client.post(path + "/decision", headers=HEADERS, json={"decision": decision}, **kwargs)


def error(response, status, code):
    safe(response, status)
    assert response.json()["code"] == code


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_commits_then_returns_public_result_and_detail(local_client, ready, engine, decision):
    (path, file, proposal), sessions, _ = ready
    before = file.read_bytes()
    # 伪造其他身份字段不会替换依赖解析出的本地身份。
    response = local_client.post(path + "/decision", json={"decision": decision}, headers=HEADERS | {
        "X-User-ID": "99999", "Cookie": "agent_session=PRIVATE", "Authorization": "PRIVATE",
        "Content-Type": "application/json; charset=utf-8",
    })
    safe(response, 200)
    assert response.json() == {
        "proposal_id": proposal.proposal_id, "workspace_id": proposal.workspace_id,
        "task_id": proposal.task_id, "status": decision,
    }
    assert stored(engine) == decision
    detail = local_client.get(path, headers=HEADERS)
    safe(detail, 200)
    assert detail.json()["status"] == decision
    assert file.read_bytes() == before
    assert len(sessions) == 1 and sessions[0].closed
    assert str(file.parent) not in response.text and "set-cookie" not in response.headers


@pytest.mark.parametrize("body", [
    {}, {"decision": "pending"}, {"decision": "APPROVED"}, {"decision": None},
    {"decision": True}, {"decision": 1}, {"decision": []}, [], "PRIVATE", None,
    {"decision": "approved", "user_id": 1},
    {"decision": "approved", "proposal_id": "PRIVATE"},
    {"decision": "approved", "proposed_content": "PRIVATE"},
])
def test_strict_body_rejected_before_service(local_client, ready, engine, monkeypatch, body):
    path = ready[0][0]
    monkeypatch.setattr(routes, "decide_task_file_edit_proposal", lambda **kw: pytest.fail("invalid body reached service"))
    # json=None在httpx中代表无正文；显式发送null覆盖JSON空值校验。
    if body is None:
        response = local_client.post(path + "/decision", headers=HEADERS | {"Content-Type": "application/json"}, content="null")
    else:
        response = local_client.post(path + "/decision", headers=HEADERS, json=body)
    error(response, 422, "invalid_proposal_decision_input")
    assert stored(engine) == "pending"


@pytest.mark.parametrize("index", [2, 4, 6])
@pytest.mark.parametrize("identifier", ["PRIVATE", "A" * 32, "a" * 32 + "%0A"])
def test_path_validation_precedes_service(local_client, ready, monkeypatch, index, identifier):
    parts = ready[0][0].split("/")
    parts[index] = identifier
    monkeypatch.setattr(routes, "decide_task_file_edit_proposal", lambda **kw: pytest.fail("invalid path reached service"))
    error(send(local_client, "/".join(parts)), 422, "invalid_proposal_decision_input")


@pytest.mark.parametrize("query", ["user_id=1", "decision=rejected", "x=1&x=2"])
def test_query_cannot_supply_alternate_input(local_client, ready, monkeypatch, query):
    monkeypatch.setattr(routes, "decide_task_file_edit_proposal", lambda **kw: pytest.fail("query reached service"))
    response = local_client.post(ready[0][0] + "/decision?" + query, headers=HEADERS, json={"decision": "approved"})
    error(response, 422, "invalid_proposal_decision_input")


@pytest.mark.parametrize("kind", ["token-missing", "token-wrong", "host", "origin-missing", "origin-foreign", "account"])
def test_access_boundary_before_service(local_client, ready, monkeypatch, kind):
    headers = dict(HEADERS)
    if kind == "token-missing":
        headers.pop("X-Local-Runtime-Token")
    elif kind == "token-wrong":
        headers["X-Local-Runtime-Token"] = "b" * 64
    elif kind == "host":
        headers["Host"] = "evil.test"
    elif kind == "origin-missing":
        headers.pop("Origin")
    elif kind == "origin-foreign":
        headers["Origin"] = "https://evil.test"
    else:
        monkeypatch.setattr(settings, "app_mode", "account")
    monkeypatch.setattr(routes, "decide_task_file_edit_proposal", lambda **kw: pytest.fail("untrusted request reached service"))
    safe(local_client.post(ready[0][0] + "/decision", headers=headers, json={"decision": "approved"}), 403)


@pytest.mark.parametrize("content_type,content,status,code", [
    ("text/plain", '{"decision":"approved"}', 415, "unsupported_workspace_content_type"),
    ("application/json", '{"decision":PRIVATE', 422, "invalid_proposal_decision_input"),
    ("application/json", "", 422, "invalid_proposal_decision_input"),
])
def test_content_boundary(local_client, ready, monkeypatch, content_type, content, status, code):
    monkeypatch.setattr(routes, "decide_task_file_edit_proposal", lambda **kw: pytest.fail("bad content reached service"))
    response = local_client.post(ready[0][0] + "/decision", headers=HEADERS | {"Content-Type": content_type}, content=content)
    error(response, status, code)


@pytest.mark.parametrize("kind", ["missing-project", "missing-task", "missing-proposal", "sibling", "owner", "conversation", "deleted"])
def test_inaccessible_and_missing_are_same_404(local_client, ready, engine, kind):
    (path, file, proposal), _, _ = ready
    if kind.startswith("missing-"):
        key = {"missing-project": proposal.workspace_id, "missing-task": proposal.task_id, "missing-proposal": proposal.proposal_id}[kind]
        path = path.replace(key, "f" * 32)
    elif kind == "sibling":
        sibling = local_client.post(f"/workspaces/{proposal.workspace_id}/tasks", headers=HEADERS, json={"title": "其他任务"})
        assert sibling.status_code == 201
        path = path.replace(proposal.task_id, sibling.json()["external_id"])
    elif kind == "deleted":
        response = local_client.delete(f"/workspaces/{proposal.workspace_id}/tasks/{proposal.task_id}", headers=HEADERS)
        assert response.status_code == 204
    else:
        with Session(engine) as session, session.begin():
            other = User(external_id="foreign")
            session.add(other)
            session.flush()
            row = session.scalar(select(Workspace if kind == "owner" else Conversation))
            row.user_id = other.id
    response = send(local_client, path)
    error(response, 404, "workspace_not_accessible")
    assert stored(engine) == (None if kind == "deleted" else "pending")
    assert file.read_bytes() == b"old\r\n"


@pytest.mark.parametrize("first", ["approved", "rejected"])
@pytest.mark.parametrize("second", ["approved", "rejected"])
def test_duplicate_and_opposite_decisions_are_conflicts(local_client, ready, engine, first, second):
    path = ready[0][0]
    safe(send(local_client, path, first), 200)
    error(send(local_client, path, second), 409, "proposal_state_conflict")
    assert stored(engine) == first


@pytest.mark.parametrize("kind", ["binding", "unbound", "truncated"])
def test_approval_guard_then_rejection_remains_available(local_client, ready, engine, kind):
    path, file, _ = ready[0]
    with Session(engine) as session, session.begin():
        if kind == "truncated":
            session.scalar(select(FileEditProposal)).diff_truncated = True
        else:
            session.scalar(select(Workspace)).root_path = None if kind == "unbound" else "/PRIVATE"
    code = "proposal_diff_incomplete" if kind == "truncated" else "proposal_binding_changed"
    error(send(local_client, path), 409, code)
    assert stored(engine) == "pending"
    safe(send(local_client, path, "rejected"), 200)
    assert stored(engine) == "rejected" and file.read_bytes() == b"old\r\n"


@pytest.mark.parametrize("code,status", [
    ("proposal_decision_invalid", 422), ("proposal_state_conflict", 409),
    ("proposal_diff_incomplete", 409), ("PRIVATE-unknown", 500),
])
def test_service_error_messages_are_not_reflected(local_client, ready, monkeypatch, caplog, code, status):
    def fail(**kwargs):
        raise decisions.ProposalDecisionError(code, "PRIVATE directory SQL and content")

    monkeypatch.setattr(routes, "decide_task_file_edit_proposal", fail)
    expected = code if status != 500 else "proposal_decision_uncertain"
    error(send(local_client, ready[0][0]), status, expected)
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("phase", ["database-error", "before-commit", "after-commit", "response-validation", "fastapi-response"])
def test_uncertain_response_never_claims_rollback_or_retries(local_client, ready, engine, monkeypatch, caplog, phase):
    (path, file, _), sessions, session_class = ready
    original = routes.decide_task_file_edit_proposal
    calls = []

    def before_commit(session):
        raise RuntimeError("PRIVATE commit unavailable")

    def invoke(**kwargs):
        calls.append(kwargs)
        if phase == "database-error":
            # 真实PostgreSQL错误发生在事务中，验证错误正文与SQL不进入HTTP响应。
            with decisions.SessionLocal.begin() as session:
                session.execute(text("SELECT 1 / 0"))
        result = original(**kwargs)
        if phase == "after-commit":
            raise RuntimeError("PRIVATE response unavailable")
        if phase == "response-validation":
            return replace(result, status="PRIVATE-invalid")
        return result

    if phase == "fastapi-response":
        # 路由已完成，注册时保存的真实response_model在框架阶段拒绝畸形返回值。
        monkeypatch.setattr(routes, "FileEditProposalDecisionResponse", SimpleNamespace(
            model_validate=lambda value: {"status": "PRIVATE"},
        ))
    if phase == "before-commit":
        event.listen(session_class, "before_commit", before_commit)
    monkeypatch.setattr(routes, "decide_task_file_edit_proposal", invoke)
    try:
        response = send(local_client, path)
    finally:
        if phase == "before-commit":
            event.remove(session_class, "before_commit", before_commit)
    error(response, 500, "proposal_decision_uncertain")
    assert "勿直接重复提交" in response.json()["message"]
    assert len(calls) == 1 and "PRIVATE" not in caplog.text
    expected = "approved" if phase in {"after-commit", "response-validation", "fastapi-response"} else "pending"
    assert stored(engine) == expected
    # 同一HTTP错误可能对应不同持久化结果，详情查询用于核对事实。
    detail = local_client.get(path, headers=HEADERS)
    safe(detail, 200)
    assert detail.json()["status"] == expected
    assert sessions and all(s.closed for s in sessions)
    assert file.read_bytes() == b"old\r\n"


def test_openapi_decision_contract(local_client):
    schema = local_client.get("/openapi.json", headers=HEADERS).json()
    route = schema["paths"][
        "/workspaces/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}/decision"
    ]["post"]
    assert {"200", "403", "404", "409", "415", "422", "500"} <= route["responses"].keys()
    assert route["requestBody"]["required"]
    request = schema["components"]["schemas"]["FileEditProposalDecisionRequest"]
    assert request["additionalProperties"] is False
    assert request["properties"]["decision"]["enum"] == ["approved", "rejected"]
    assert {parameter["name"] for parameter in route["parameters"]} == {"workspace_id", "task_id", "proposal_id"}
