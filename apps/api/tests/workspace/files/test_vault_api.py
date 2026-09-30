"""已注册 Vault API：真实本机身份、隔离 PostgreSQL 与文件系统边界。"""

from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.local_boundary import local_access_boundary
from app.models import Conversation, User, Workspace
from app.routers.workspace import vault as route
from app.routers.workspace.router import router
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.workspace.directory import workspace_path
from app.services.workspace.files.workspace_file import WorkspaceFileError
from tests.assertions import require_value
from tests.tasks.test_task_deletion_service import target
from tests.workspace.directory.test_workspace_path import database


__all__ = ["database", "target"]
TOKEN = "a" * 64
HEADERS = {"X-Local-Runtime-Token": TOKEN, "Origin": "http://localhost:3000"}


@pytest.fixture
def root(tmp_path):
    directory = tmp_path.resolve() / "vault"
    directory.mkdir()
    (directory / "note.md").write_bytes("# Docker\r\n资源清理\n".encode())
    hidden = directory / ".obsidian"
    hidden.mkdir()
    (hidden / "config.json").write_text("PRIVATE")
    return directory


@pytest.fixture
def api(engine, target, database, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_runtime_token", SecretStr(TOKEN))
    monkeypatch.setattr(dependencies, "SessionLocal", lambda: Session(engine))
    with Session(engine) as session, session.begin():
        owner = require_value(session.get(User, target["user_id"]))
        owner.external_id = LOCAL_USER_ID
        owner.username = owner.password_hash = None

    app = FastAPI()
    app.middleware("http")(local_access_boundary)
    app.include_router(router)
    base = f"/workspaces/{target['workspace_id']}/tasks/{target['task_id']}/vault"
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, base


def get(api, endpoint="files", params=None, headers=None):
    return api[0].get(
        f"{api[1]}/{endpoint}", params=params,
        headers=HEADERS if headers is None else headers,
    )


def test_registered_api_source_and_no_business_or_file_writes(api, root, database, engine):
    before = [(p.relative_to(root), p.read_bytes(), p.stat().st_mtime_ns)
              for p in root.rglob("*") if p.is_file()]
    statements = []

    def capture(conn, cursor, sql, parameters, context, executemany):
        statements.append(sql.lower().lstrip())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        files = get(api)
        response = get(api, "document", {"path": "./note.md"})
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert files.status_code == response.status_code == 200
    assert files.json()["paths"] == ["note.md"]
    assert files.json()["truncated"] is False
    data = response.json()
    assert data["source"] == {
        "workspace_id": "a" * 32, "relative_path": "note.md",
        "sha256": sha256((root / "note.md").read_bytes()).hexdigest(),
        "start_line": 1, "end_line": 2,
    }
    assert data["content"].encode() == (root / "note.md").read_bytes()
    assert data["byte_count"] == len(data["content"].encode())
    assert data["task_id"] == "b" * 32
    assert str(root) not in response.text and TOKEN not in response.text
    assert files.headers["cache-control"] == response.headers["cache-control"] == "no-store"
    # 本机身份会执行既有 INSERT ON CONFLICT；业务操作仅 SELECT。
    assert statements and all(
        sql.startswith("select") or ("insert into users" in sql and "on conflict" in sql)
        for sql in statements
    )
    assert database[0] and all(session.closed for session in database[0])
    after = [(p.relative_to(root), p.read_bytes(), p.stat().st_mtime_ns)
             for p in root.rglob("*") if p.is_file()]
    assert before == after


@pytest.mark.parametrize("kind", ["missing-task", "wrong-project", "foreign-owner", "foreign-conversation"])
def test_real_authorization_rechecked_before_filesystem(api, engine, target, monkeypatch, kind):
    path = api[1] + "/files"
    with Session(engine) as session, session.begin():
        if kind == "missing-task":
            path = path.replace(target["task_id"], "f" * 32)
        elif kind == "wrong-project":
            session.add(Workspace(external_id="f" * 32, name="other", user_id=target["user_id"]))
            path = path.replace(target["workspace_id"], "f" * 32)
        elif kind == "foreign-owner":
            require_value(session.scalar(select(Workspace))).user_id = target["other_id"]
        else:
            require_value(session.get(Conversation, target["conversation_pk"])).user_id = target["other_id"]
    monkeypatch.setattr(workspace_path, "_resolve_bound_path", lambda **kwargs: pytest.fail("unauthorized I/O"))
    response = api[0].get(path, headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["code"] == "workspace_not_accessible"


def test_unbound_does_not_fallback_to_cwd(api, engine, monkeypatch):
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(Workspace))).root_path = None
    monkeypatch.setattr(workspace_path, "_resolve_bound_path", lambda **kwargs: pytest.fail("unbound I/O"))
    response = get(api)
    assert response.status_code == 422
    assert response.json()["code"] == "workspace_directory_unbound"


@pytest.mark.parametrize("kind", ["internal", "hidden-config", "outside", "directory"])
def test_real_symlinks_are_not_followed(api, root, kind):
    link = root / "alias.md"
    if kind == "internal":
        link.symlink_to(root / "note.md")
        relative = "alias.md"
    elif kind == "hidden-config":
        link.symlink_to(root / ".obsidian" / "config.json")
        relative = "alias.md"
    elif kind == "outside":
        outside = root.parent / "private.md"
        outside.write_text("PRIVATE")
        link.symlink_to(outside)
        relative = "alias.md"
    else:
        link.symlink_to(root, target_is_directory=True)
        relative = "alias.md/note.md"
    assert get(api).json()["paths"] == ["note.md"]
    response = get(api, "document", {"path": relative})
    assert response.status_code in {422, 503}
    assert response.json()["code"] in {"workspace_path_unavailable", "path_outside_workspace"}
    assert "PRIVATE" not in response.text and str(root.parent) not in response.text


def test_link_replacement_after_resolution_is_rejected(api, root, monkeypatch):
    original = workspace_path._resolve_bound_path

    def replaced(**kwargs):
        path = original(**kwargs)
        if path == root / "note.md":
            path.unlink()
            path.symlink_to(root / ".obsidian" / "config.json")
        return path

    monkeypatch.setattr(workspace_path, "_resolve_bound_path", replaced)
    response = get(api, "document", {"path": "note.md"})
    assert response.status_code == 422
    assert response.json()["code"] == "file_not_regular"
    assert "PRIVATE" not in response.text


@pytest.mark.parametrize("endpoint,params", [
    ("files", {"user_id": "1"}), ("files", {"limit": "1000"}),
    ("document", None), ("document", [("path", "note.md"), ("path", "note.md")]),
    ("document", {"path": "note.md", "root": "/private"}),
    ("document", {"path": "../private.md"}),
    ("document", {"path": "/private.md"}),
    ("document", {"path": ".obsidian/config.json"}),
    ("document", {"path": "image.png"}),
])
def test_query_and_scope_rejections(api, endpoint, params):
    response = get(api, endpoint, params)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers", [
    {}, HEADERS | {"X-Local-Runtime-Token": "b" * 64},
    HEADERS | {"Host": "evil.test"}, HEADERS | {"Origin": "https://evil.test"},
])
def test_real_local_access_boundary(api, monkeypatch, headers):
    monkeypatch.setattr(route, "list_vault_markdown", lambda **kwargs: pytest.fail("boundary first"))
    assert get(api, headers=headers).status_code == 403


def test_account_mode_rejected_before_identity(api, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "account")
    monkeypatch.setattr(dependencies, "SessionLocal", lambda: pytest.fail("local mode first"))
    response = get(api)
    assert response.status_code == 403 and response.json()["code"] == "local_mode_required"


@pytest.mark.parametrize("error,status,code", [
    (WorkspaceFileError("file_access_denied", "没有权限读取目标文件"), 403, "file_access_denied"),
    (WorkspaceFileError("file_changed", "文件在读取期间发生变化，请重新读取"), 409, "file_changed"),
    (WorkspaceFileError("file_read_unsupported", "当前平台尚不支持受限文件读取"), 501, "file_read_unsupported"),
    (WorkspaceFileError("file_unavailable", "目标文件暂时无法读取"), 503, "file_unavailable"),
    (RuntimeError("PRIVATE/path SQL token"), 500, "vault_read_failed"),
])
def test_read_failure_never_becomes_empty_success(api, monkeypatch, error, status, code):
    def failed(**kwargs):
        raise error
    monkeypatch.setattr(route, "read_vault_markdown", failed)
    response = get(api, "document", {"path": "note.md"})
    assert response.status_code == status and response.json()["code"] == code
    assert "PRIVATE" not in response.text and response.headers["cache-control"] == "no-store"
