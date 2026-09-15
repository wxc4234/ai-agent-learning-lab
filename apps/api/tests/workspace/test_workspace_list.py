"""列表读取：真实账号/本地身份、稳定排序、截断与只读边界。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User, Workspace
from app.repositories.workspace.workspace_repository import list_owned_workspaces
from app.routers.workspace import workspace
import tests.workspace.test_workspace_api as api_fixtures
from tests.workspace.test_workspace_api import assert_safe, headers
import tests.local.test_local_mode as local_fixtures
from tests.local.test_local_mode import HEADERS

# 复用真实认证与本地模式隔离夹具，不重复创建另一套测试环境。
lab = api_fixtures.lab
local_client = local_fixtures.local_client


def seed(engine, user_id, count, *, newer=False):
    result = []
    with Session(engine) as session:
        for index in range(count):
            item = Workspace(
                external_id=uuid4().hex, user_id=user_id, name=f"项目 {index}",
                created_at=datetime(2026, 9, 15, tzinfo=timezone.utc) + timedelta(days=int(newer), seconds=index // 2),
            )
            session.add(item)
            session.flush()
            result.append(item.external_id)
        session.commit()
    return list(reversed(result))


def test_empty_list(lab):
    client, identities, *_ = lab
    response = client.get("/workspaces", headers=headers(identities[0]))
    assert_safe(response, 200)
    assert response.json() == {"items": [], "has_more": False}


@pytest.mark.parametrize("count,limit,expected,more", [
    (1, 1, 1, False), (2, 1, 1, True), (20, None, 20, False),
    (21, None, 20, True), (100, 100, 100, False), (101, 100, 100, True),
])
def test_limit_order_and_owner_filter_before_truncation(lab, engine, count, limit, expected, more):
    client, identities, *_ = lab
    owner, other = identities
    ordered = seed(engine, owner.user.id, count)
    seed(engine, other.user.id, 3, newer=True)
    path = "/workspaces" + (f"?limit={limit}&user_id={other.user.id}" if limit else "")
    response = client.get(path, headers=headers(owner))
    assert_safe(response, 200)
    data = response.json()
    assert set(data) == {"items", "has_more"}
    assert data["has_more"] is more
    assert [item["external_id"] for item in data["items"]] == ordered[:expected]
    assert all(set(item) == {"external_id", "name", "created_at"} for item in data["items"])


@pytest.mark.parametrize("limit", ["0", "-1", "101", "abc", "1.5", ""])
def test_invalid_query(lab, limit):
    client, identities, *_ = lab
    response = client.get(f"/workspaces?limit={limit}", headers=headers(identities[0]))
    assert_safe(response, 422, "invalid_workspace_input")
    assert response.json()["message"] == "工作空间请求参数不符合要求"


def test_account_mode_requires_identity(lab):
    client, *_ = lab
    assert_safe(client.get("/workspaces"), 401, "invalid_login_session")


@pytest.mark.parametrize("failure", ["database", "invalid response"])
def test_safe_query_failure_and_session_cleanup(lab, monkeypatch, failure):
    client, identities, auth_sessions, business_sessions, *_ = lab
    def broken(**kwargs):
        if failure == "database":
            raise RuntimeError("PRIVATE SQL")
        return [SimpleNamespace(external_id=None, name="PRIVATE", created_at=None)]
    monkeypatch.setattr(workspace, "list_owned_workspaces", broken)
    response = client.get("/workspaces", headers=headers(identities[0]))
    assert_safe(response, 500, "workspace_list_failed")
    assert all(session.was_closed for session in auth_sessions + business_sessions)


def test_local_list_keeps_registered_resources_separate(local_client, engine):
    with Session(engine) as session:
        user = User(external_id=uuid4().hex)
        session.add(user)
        session.flush()
        other_id = user.id
        session.commit()
    seed(engine, other_id, 2, newer=True)
    created = local_client.post("/workspaces", headers=HEADERS, json={"name": "本机项目"})
    assert created.status_code == 201
    response = local_client.get("/workspaces", headers=HEADERS)
    assert_safe(response, 200)
    assert response.json() == {"items": [created.json()], "has_more": False}
    assert local_client.get("/workspaces").status_code == 403


def test_repository_does_not_flush_or_commit_pending_work(engine):
    with Session(engine) as session:
        user = User(external_id=uuid4().hex)
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()
    expected = seed(engine, user_id, 2)
    with Session(engine, autoflush=True) as session:
        pending = Workspace(external_id=uuid4().hex, user_id=user_id, name="尚未提交")
        session.add(pending)
        result = list_owned_workspaces(session, user_id=user_id, limit=10)
        assert [item.external_id for item in result] == expected
        assert pending in session.new and pending.id is None
        assert session.in_transaction()
        with Session(engine) as observer:
            assert observer.scalar(select(func.count()).select_from(Workspace)) == 2


@pytest.mark.parametrize("limit", [0, -1, 102])
def test_repository_rejects_invalid_limit_before_query(engine, limit):
    with Session(engine) as session:
        with pytest.raises(ValueError):
            list_owned_workspaces(session, user_id=1, limit=limit)
        assert not session.in_transaction()
