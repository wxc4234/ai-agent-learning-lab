"""local详情HTTP：真实授权查询、公开响应与异常脱敏。"""

from dataclasses import replace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation, FileEditProposal, User, Workspace
from app.routers.workspace import proposals as routes
from app.services.workspace.proposals import file_edit_proposal_service as service
from app.services.workspace.directory import workspace_path
from tests.local.test_local_mode import HEADERS, TOKEN
from tests.local import test_local_mode as local_tests

local_client = local_tests.local_client


@pytest.fixture
def saved(local_client, engine, tmp_path, monkeypatch):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(workspace_path, "SessionLocal", factory)
    root = tmp_path.resolve() / "project"
    root.mkdir()
    file = root / "file.txt"
    file.write_bytes(b"old\r\n")
    created = local_client.post("/workspaces", headers=HEADERS, json={"name": "提案HTTP"})
    assert created.status_code == 201
    wid = created.json()["external_id"]
    assert local_client.put(f"/workspaces/{wid}/directory", headers=HEADERS, json={"root_path": str(root)}).status_code == 200
    response = local_client.post(f"/workspaces/{wid}/tasks", headers=HEADERS, json={"title": "任务"})
    assert response.status_code == 201
    tid = response.json()["external_id"]
    with Session(engine) as session:
        uid = session.scalar(select(Workspace.user_id).where(Workspace.external_id == wid))
    proposal = service.create_task_file_edit_proposal(user_id=uid, workspace_id=wid, task_id=tid, relative_path="file.txt", old_text="old", new_text="new")
    path = f"/workspaces/{wid}/tasks/{tid}/file-edit-proposals/{proposal.proposal_id}"
    return path, file, proposal


def safe(response, status):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text
    if status != 200:
        assert set(response.json()) == {"code", "message"}


def test_real_response_is_readonly_public_and_identity_cannot_be_injected(local_client, saved, engine, monkeypatch):
    path, file, proposal = saved
    sessions = []
    statements = []

    class ReadSession(Session):
        closed = False

        def commit(self):
            pytest.fail("query must not commit")

        def close(self):
            super().close()
            self.closed = True

    def factory():
        session = ReadSession(engine)
        sessions.append(session)
        return session

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower().strip())

    monkeypatch.setattr(service, "SessionLocal", factory)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = local_client.get(path, headers={"X-Local-Runtime-Token": TOKEN, "X-User-ID": "999", "Cookie": "agent_session=PRIVATE"}, params={"user_id": 999})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    safe(response, 200)
    data = response.json()
    assert set(data) == {"proposal_id", "workspace_id", "task_id", "relative_path", "status", "baseline_sha256", "proposed_sha256", "diff", "diff_truncated", "created_at"}
    assert data["proposal_id"] == proposal.proposal_id and data["status"] == "pending"
    assert data["baseline_sha256"] == proposal.baseline_sha256
    assert data["proposed_sha256"] == proposal.proposed_sha256
    assert data["diff"] and not data["diff_truncated"]
    assert isinstance(data["created_at"], str)
    # local身份依赖有幂等INSERT；提案查询本身必须只读，不将身份初始化误报为业务写入。
    assert statements and all(
        (sql.startswith("select") and "for update" not in sql)
        or (sql.startswith("insert into users ") and "on conflict" in sql and "do nothing" in sql)
        for sql in statements
    )
    proposal_queries = [sql for sql in statements if "file_edit_proposals" in sql]
    assert len(proposal_queries) == 1 and proposal_queries[0].startswith("select")
    assert sessions and all(s.closed and not s.in_transaction() for s in sessions)
    assert str(file.parent) not in response.text and "proposed_content" not in data
    assert file.read_bytes() == b"old\r\n"


@pytest.mark.parametrize("kind", ["missing-project", "missing-task", "missing-proposal", "sibling", "owner", "conversation", "deleted"])
def test_unknown_and_inaccessible_are_same_404(local_client, saved, engine, kind):
    path, _, proposal = saved
    if kind.startswith("missing-"):
        old = {"missing-project": proposal.workspace_id, "missing-task": proposal.task_id, "missing-proposal": proposal.proposal_id}[kind]
        path = path.replace(old, "f" * 32)
    elif kind == "sibling":
        sibling = local_client.post(f"/workspaces/{proposal.workspace_id}/tasks", headers=HEADERS, json={"title": "其他任务"}).json()
        path = path.replace(proposal.task_id, sibling["external_id"])
    elif kind == "deleted":
        response = local_client.delete(f"/workspaces/{proposal.workspace_id}/tasks/{proposal.task_id}", headers=HEADERS)
        assert response.status_code == 204
    else:
        with Session(engine) as session, session.begin():
            other = User(external_id="other")
            session.add(other)
            session.flush()
            if kind == "owner":
                session.scalar(select(Workspace)).user_id = other.id
            else:
                session.scalar(select(Conversation)).user_id = other.id
    response = local_client.get(path, headers=HEADERS)
    safe(response, 404)
    assert response.json() == {"code": "workspace_not_accessible", "message": "工作空间不存在或不可访问"}


@pytest.mark.parametrize("index", [2, 4, 6])
@pytest.mark.parametrize("invalid", ["PRIVATE", "A" * 32, "a" * 33])
def test_invalid_identifiers(local_client, saved, monkeypatch, index, invalid):
    parts = saved[0].split("/")
    parts[index] = invalid
    monkeypatch.setattr(routes, "get_task_file_edit_proposal", lambda **kwargs: pytest.fail("invalid input reached service"))
    safe(local_client.get("/".join(parts), headers=HEADERS), 422)


@pytest.mark.parametrize("headers", [{}, {"X-Local-Runtime-Token": "b" * 64}, HEADERS | {"Host": "evil.test"}, HEADERS | {"Origin": "https://evil.test"}])
def test_local_boundary_rejects_before_service(local_client, saved, monkeypatch, headers):
    monkeypatch.setattr(routes, "get_task_file_edit_proposal", lambda **kwargs: pytest.fail("untrusted access reached service"))
    safe(local_client.get(saved[0], headers=headers), 403)


@pytest.mark.parametrize("kind", ["exception", "invalid-status"])
def test_internal_failure_and_unknown_status_are_safe_500(local_client, saved, monkeypatch, kind):
    original = routes.get_task_file_edit_proposal

    def fail(**kwargs):
        if kind == "exception":
            raise RuntimeError("PRIVATE SQL and directory")
        # 验证HTTP运行时仍拒绝未知状态，而非只修复静态类型提示。
        return replace(original(**kwargs), status="PRIVATE-approved")

    monkeypatch.setattr(routes, "get_task_file_edit_proposal", fail)
    safe(local_client.get(saved[0], headers=HEADERS), 500)


def test_historical_diff_survives_file_deletion_and_truncation(local_client, saved, engine):
    path, file, _ = saved
    file.unlink()
    with Session(engine) as session, session.begin():
        row = session.scalar(select(FileEditProposal))
        row.diff = "x" * 16384
        row.diff_truncated = True
        session.scalar(select(Workspace)).root_path = None
    response = local_client.get(path, headers=HEADERS)
    safe(response, 200)
    assert response.json()["diff"] == "x" * 16384 and response.json()["diff_truncated"] is True
    assert not file.exists()


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_committed_decision_is_visible_through_local_http(local_client, saved, engine, monkeypatch, decision):
    from app.services.workspace.proposals import file_edit_proposal_decision as decisions

    path, file, proposal = saved
    monkeypatch.setattr(decisions, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    with Session(engine) as session:
        user_id = session.scalar(select(Workspace.user_id).where(Workspace.external_id == proposal.workspace_id))
    decisions.decide_task_file_edit_proposal(
        user_id=user_id, workspace_id=proposal.workspace_id, task_id=proposal.task_id,
        proposal_id=proposal.proposal_id, decision=decision,
    )
    response = local_client.get(path, headers=HEADERS)
    safe(response, 200)
    assert response.json()["status"] == decision
    assert response.json()["proposal_id"] == proposal.proposal_id
    assert "proposed_content" not in response.json() and str(file.parent) not in response.text
    assert file.read_bytes() == b"old\r\n"
