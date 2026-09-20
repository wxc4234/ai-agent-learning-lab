"""本地身份与访问边界，使用真实应用和隔离 PostgreSQL。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings, Settings
from app.main import app
from app import main
from app.routers.chat import chat_execution
from app.models import User, Workspace
from app.routers.workspace import workspace
from app.services.auth.local_identity import LOCAL_USER_ID, resolve_local_identity

TOKEN = "a" * 64
HEADERS = {"X-Local-Runtime-Token": TOKEN, "Origin": "http://localhost:3000"}


@pytest.fixture
def local_client(engine, monkeypatch, account_mode_baseline):
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_runtime_token", SecretStr(TOKEN))
    monkeypatch.setattr(dependencies, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(workspace, "SessionLocal", lambda: Session(engine))
    from app.services.tasks import task_workspace
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(task_workspace, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(chat_execution, "SessionLocal", lambda: Session(engine))
    # 本夹具使用隔离 schema 的 ORM 表；迁移启动检查由 migrations 专项验证。
    # 进入 lifespan，确保预算及请求始终运行在同一个事件循环。
    monkeypatch.setattr(main, "check_database_ready", lambda: None)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def test_local_identity_stable_and_does_not_adopt_registered_user(local_client, engine):
    with Session(engine) as session:
        session.add(User(external_id="existing-account", username="existing", password_hash="hash"))
        session.commit()
    first = local_client.get("/auth/me", headers=HEADERS)
    second = local_client.get("/auth/me", headers=HEADERS | {"Cookie": "agent_session=forged"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"external_id": LOCAL_USER_ID, "username": "本机用户"}
    response = local_client.post("/workspaces?user_id=1", headers=HEADERS, json={"name": " 本地项目 "})
    assert response.status_code == 201
    assert response.json()["name"] == "本地项目"
    assert "set-cookie" not in response.headers
    with Session(engine) as session:
        local = session.scalar(select(User).where(User.external_id == LOCAL_USER_ID))
        assert session.scalar(select(Workspace.user_id)) == local.id
        assert local.username is None and local.password_hash is None
        assert session.scalar(select(func.count()).select_from(User)) == 2


@pytest.mark.parametrize("headers", [{}, {"X-Local-Runtime-Token": "b" * 64}, HEADERS | {"Origin": "https://evil.test"}, HEADERS | {"Host": "evil.test"}])
def test_rejects_untrusted_local_access_without_creating_identity(local_client, engine, headers):
    response = local_client.post("/workspaces", headers=headers, json={"name": "blocked"})
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert TOKEN not in response.text
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("path", ["/auth/login", "/auth/register", "/auth/logout"])
def test_local_mode_disables_account_mutations(local_client, path):
    response = local_client.post(path, headers=HEADERS, json={})
    assert response.status_code == 403


def test_concurrent_identity_initialization(engine):
    def resolve(_):
        with Session(engine) as session:
            return resolve_local_identity(session)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(resolve, range(8)))
    assert len({result.id for result in results}) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_existing_transaction_is_not_rolled_back(engine):
    with Session(engine) as session:
        session.begin()
        with pytest.raises(RuntimeError, match="fresh session"):
            resolve_local_identity(session)
        assert session.in_transaction()


@pytest.mark.parametrize("token", ["", "short", "x" * 64])
def test_local_configuration_requires_token(token):
    with pytest.raises(ValueError):
        Settings(APP_MODE="local", LOCAL_RUNTIME_TOKEN=token)
