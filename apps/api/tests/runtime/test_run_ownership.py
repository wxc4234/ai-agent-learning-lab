"""Run authorization and terminal races on isolated PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import dependencies
from app.models import AgentRun, AgentRunEvent, LoginSession, User
from app.repositories.runtime import run_repository as repository
from app.routers.runtime import runs
from app.schemas import LoginRequest, RegisterRequest
from app.services.auth.login_session_service import issue_login_session
from app.services.auth.registration_service import register_user


@pytest.fixture
def lab(engine, monkeypatch):
    factory = sessionmaker(engine)
    monkeypatch.setattr(dependencies, "SessionLocal", factory)
    monkeypatch.setattr(repository, "SessionLocal", factory)
    identities = []
    for name in ("运行甲", "运行乙"):
        with Session(engine) as session:
            register_user(session, RegisterRequest(username=name, password="Run-Ownership-2026!"))
        with Session(engine) as session:
            identities.append(issue_login_session(session, LoginRequest(username=name, password="Run-Ownership-2026!")))
    owner, other = identities
    run_id = repository.create_agent_run(user_id=owner.user.id, session_id="owned-run")
    published = AsyncMock()
    monkeypatch.setattr(runs, "publish_run_cancellation", published)
    app = FastAPI()
    app.include_router(runs.router)
    with TestClient(app) as client:
        yield client, owner, other, run_id, published


def headers(identity):
    return {"Origin": "http://localhost:3000", "Cookie": f"agent_session={identity.token.get_secret_value()}"}


def access(client, run_id, identity, operation="cancel", body=None):
    if operation == "read":
        return client.get(f"/runs/{run_id}", headers=headers(identity))
    return client.post(f"/runs/{run_id}/cancel", headers=headers(identity), json=body or {"reason": "user"})


def snapshot(run_id, owner):
    return repository.load_run_timeline(run_id, user_id=owner.user.id)


@pytest.mark.parametrize("operation", ["read", "cancel"])
@pytest.mark.parametrize("state", ["running", "done", "aborted", "error"])
def test_foreign_and_missing_are_identical_without_side_effects(lab, operation, state):
    client, owner, other, run_id, published = lab
    if state != "running":
        repository.finish_agent_run(run_id, state)
    before = snapshot(run_id, owner)
    foreign = access(client, run_id, other, operation, {"reason": "user", "user_id": owner.user.id})
    missing = access(client, run_id + 9999, other, operation)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"code": "run_not_accessible", "message": "运行不存在或不可访问"}
    assert foreign.headers["cache-control"] == "no-store"
    assert snapshot(run_id, owner) == before
    published.assert_not_awaited()


@pytest.mark.parametrize("operation", ["read", "cancel"])
@pytest.mark.parametrize("credential", ["missing", "unknown", "expired", "revoked"])
def test_invalid_authentication_has_no_effect(lab, engine, operation, credential):
    client, owner, _, run_id, published = lab
    before = snapshot(run_id, owner)
    request_headers = headers(owner)
    if credential == "missing":
        request_headers.pop("Cookie")
    elif credential == "unknown":
        request_headers["Cookie"] = "agent_session=" + "z" * 43
    else:
        with Session(engine) as session, session.begin():
            record = session.scalar(select(LoginSession).where(LoginSession.user_id == owner.user.id))
            if credential == "revoked":
                record.revoked_at = datetime.now(timezone.utc)
            else:
                record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
                record.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    if operation == "read":
        response = client.get(f"/runs/{run_id}", headers=request_headers)
    else:
        response = client.post(f"/runs/{run_id}/cancel", headers=request_headers, json={"reason": "user"})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert snapshot(run_id, owner) == before
    published.assert_not_awaited()


@pytest.mark.parametrize("reason,state,terminal", [("user", "aborted", "RUN_ABORTED"), ("timeout", "error", "RUN_ERROR")])
def test_owner_cancel_commits_before_publish_and_is_idempotent(lab, engine, reason, state, terminal):
    client, owner, _, run_id, published = lab

    async def check_committed(published_id, published_reason):
        assert (published_id, published_reason) == (run_id, reason)
        with Session(engine) as session:
            assert session.get(AgentRun, run_id).status == state
        assert snapshot(run_id, owner)["events"][-1]["event_type"] == terminal

    published.side_effect = check_committed
    response = access(client, run_id, owner, body={"reason": reason})
    assert response.status_code == 204 and response.content == b""
    assert response.headers["cache-control"] == "no-store"
    before = snapshot(run_id, owner)
    assert before["duration_ms"] is not None
    assert [item["event_type"] for item in before["events"]] == ["RUN_STARTED", "RUN_CANCELLATION_REQUESTED", terminal]
    assert access(client, run_id, owner, body={"reason": "timeout"}).status_code == 204
    repository.finish_agent_run(run_id, "done")
    assert snapshot(run_id, owner) == before
    published.assert_awaited_once()
    read = access(client, run_id, owner, "read")
    assert read.status_code == 200 and read.json()["status"] == state
    assert read.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("state", ["done", "aborted", "error"])
def test_already_terminal_owner_run_is_unchanged(lab, state):
    client, owner, _, run_id, published = lab
    repository.finish_agent_run(run_id, state)
    before = snapshot(run_id, owner)
    assert access(client, run_id, owner).status_code == 204
    assert snapshot(run_id, owner) == before
    published.assert_not_awaited()


def test_legacy_anonymous_run_is_not_claimed(lab, engine):
    client, owner, _, _, published = lab
    with Session(engine) as session, session.begin():
        legacy = User(external_id="legacy-anonymous")
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id
    run_id = repository.create_agent_run(user_id=legacy_id, session_id="legacy-session")
    before = repository.load_run_timeline(run_id, user_id=legacy_id)
    for operation in ("read", "cancel"):
        assert access(client, run_id, owner, operation).status_code == 404
    assert repository.load_run_timeline(run_id, user_id=legacy_id) == before
    published.assert_not_awaited()


@pytest.mark.parametrize("operation", ["read", "cancel"])
def test_database_failure_is_safe_500(lab, engine, monkeypatch, caplog, operation):
    client, owner, _, run_id, published = lab
    before = snapshot(run_id, owner)

    def failure(*args, **kwargs):
        with Session(engine) as session:
            session.execute(text("SELECT * FROM private_run_secret_table"))

    monkeypatch.setattr(runs, "load_run_timeline" if operation == "read" else "request_run_cancellation", failure)
    response = access(client, run_id, owner, operation)
    assert response.status_code == 500
    assert "private_run_secret_table" not in response.text + caplog.text
    assert response.headers["cache-control"] == "no-store"
    assert snapshot(run_id, owner) == before
    published.assert_not_awaited()


def test_redis_failure_reports_503_after_database_commit(lab, caplog):
    client, owner, _, run_id, published = lab
    published.side_effect = RedisError("private redis connection")
    response = access(client, run_id, owner)
    assert response.status_code == 503
    assert "private redis connection" not in response.text + caplog.text
    assert snapshot(run_id, owner)["status"] == "aborted"
    assert access(client, run_id, owner).status_code == 204
    published.assert_awaited_once()


@pytest.mark.parametrize("origin,content_type,body,status", [
    ("http://localhost:3000.evil.test", "application/json", '{}', 403),
    ("null", "application/json", '{}', 403),
    ("http://localhost:3000", "text/plain", '{}', 415),
    ("http://localhost:3000", "application/json", '{', 422),
    ("http://localhost:3000", "application/json", '{"reason":"private-invalid"}', 422),
])
def test_boundary_rejections_do_not_write(lab, origin, content_type, body, status):
    client, owner, _, run_id, published = lab
    before = snapshot(run_id, owner)
    response = client.post(f"/runs/{run_id}/cancel", content=body,
                           headers=headers(owner) | {"Origin": origin, "Content-Type": content_type})
    assert response.status_code == status
    assert "private-invalid" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert snapshot(run_id, owner) == before
    published.assert_not_awaited()


@pytest.mark.parametrize("operation", ["cancel", "finish"])
def test_terminal_writers_wait_for_lock_and_preserve_winner(lab, engine, operation):
    _, owner, _, run_id, _ = lab
    entered = Event()

    def query_started(connection, cursor, statement, parameters, context, executemany):
        if "FROM agent_runs" in statement:
            entered.set()

    with Session(engine) as holder:
        run = holder.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        event.listen(engine, "before_cursor_execute", query_started)
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                if operation == "cancel":
                    future = executor.submit(repository.request_run_cancellation, run_id, "user", user_id=owner.user.id)
                else:
                    future = executor.submit(repository.finish_agent_run, run_id, "done")
                assert entered.wait(5), "writer never attempted a read"
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.15)
                run.status = "error"
                run.finished_at = datetime.now(timezone.utc)
                holder.add(AgentRunEvent(run_id=run_id, event_type="RUN_ERROR", payload={"reason": "winner"}))
                holder.commit()
                assert future.result(timeout=5) is (False if operation == "cancel" else None)
            finally:
                holder.rollback()
                event.remove(engine, "before_cursor_execute", query_started)
    timeline = snapshot(run_id, owner)
    assert timeline["status"] == "error"
    assert [item["event_type"] for item in timeline["events"]] == ["RUN_STARTED", "RUN_ERROR"]


def test_failed_cancel_event_insert_rolls_back_status_and_does_not_publish(lab, caplog):
    client, owner, _, run_id, published = lab
    before = snapshot(run_id, owner)

    def fail_insert(mapper, connection, target):
        if target.event_type == "RUN_CANCELLATION_REQUESTED":
            raise RuntimeError("private event storage failure")

    event.listen(AgentRunEvent, "before_insert", fail_insert)
    try:
        response = access(client, run_id, owner)
    finally:
        event.remove(AgentRunEvent, "before_insert", fail_insert)
    assert response.status_code == 500
    assert "private event storage failure" not in response.text + caplog.text
    assert snapshot(run_id, owner) == before
    published.assert_not_awaited()


@pytest.mark.parametrize("operation", ["read", "cancel"])
def test_auth_database_fault_is_500_not_401(lab, monkeypatch, caplog, operation):
    client, owner, _, run_id, published = lab
    before = snapshot(run_id, owner)

    def failure(*args, **kwargs):
        raise RuntimeError("private auth database fault")

    monkeypatch.setattr(dependencies, "resolve_login_session", failure)
    response = access(client, run_id, owner, operation)
    assert response.status_code == 500
    assert "private auth database fault" not in response.text + caplog.text
    assert snapshot(run_id, owner) == before
    published.assert_not_awaited()


def test_two_cancellations_create_one_intent_and_one_terminal(lab):
    from threading import Barrier

    _, owner, _, run_id, _ = lab
    start = Barrier(2)

    def cancel(reason):
        start.wait(timeout=5)
        return repository.request_run_cancellation(run_id, reason, user_id=owner.user.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cancel, "user")
        second = executor.submit(cancel, "timeout")
        assert sorted([first.result(timeout=5), second.result(timeout=5)]) == [False, True]
    timeline = snapshot(run_id, owner)
    assert len(timeline["events"]) == 3
    assert timeline["events"][1]["event_type"] == "RUN_CANCELLATION_REQUESTED"
    reason = timeline["events"][1]["payload"]["reason"]
    assert timeline["status"] == ("aborted" if reason == "user" else "error")
