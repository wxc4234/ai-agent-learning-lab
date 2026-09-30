"""已注册源码清单API：真实本机身份、PostgreSQL授权、规则与受限I/O。"""

from hashlib import sha256

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.local_boundary import local_access_boundary
from app.models import Conversation, User, Workspace
from app.routers.workspace import code_inventory as route
from app.routers.workspace.router import router
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.workspace.directory import workspace_path
from app.services.workspace.files import (
    code_inventory as service,
    workspace_file,
    workspace_listing,
)
from app.services.workspace.files.workspace_file import WorkspaceFileError
from tests.assertions import require_value
from tests.tasks.test_task_deletion_service import target
from tests.workspace.directory.test_workspace_path import database


__all__ = ["database", "target"]
TOKEN = "a" * 64
HEADERS = {"X-Local-Runtime-Token": TOKEN, "Origin": "http://localhost:3000"}


@pytest.fixture
def root(tmp_path):
    project = tmp_path.resolve() / "code"
    project.mkdir()
    (project / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (project / "safe.py").write_bytes(b"print('SOURCE_PRIVATE')\r\n")
    (project / "ignored.py").write_text("PRIVATE")
    (project / ".env").write_text("PRIVATE")
    return project


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
    base = (
        f"/workspaces/{target['workspace_id']}/tasks/{target['task_id']}/code-inventory"
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, base


def get(api, **kwargs):
    return api[0].get(api[1], headers=kwargs.pop("headers", HEADERS), **kwargs)


def snapshot(root):
    return {
        str(file.relative_to(root)): (
            file.read_bytes(),
            file.stat().st_ino,
            file.stat().st_mtime_ns,
        )
        for file in root.rglob("*")
        if file.is_file() and not file.is_symlink()
    }


def test_registered_source_metadata_read_only_database_files_and_closed_sessions(
    api, root, database, engine, monkeypatch
):
    before = snapshot(root)
    statements = []
    original_read = workspace_file._read_resolved_file
    original_list = workspace_listing._list_resolved_directory

    def read(path):
        assert database[0] and all(session.closed for session in database[0])
        return original_read(path)

    def listing(path):
        assert database[0] and all(session.closed for session in database[0])
        return original_list(path)

    def capture(connection, cursor, sql, parameters, context, executemany):
        statements.append(sql.lower().lstrip())

    monkeypatch.setattr(workspace_file, "_read_resolved_file", read)
    monkeypatch.setattr(workspace_listing, "_list_resolved_directory", listing)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = get(api)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    body = response.json()
    assert (
        body["source"] == "authorized_code_inventory"
        and body["policy"] == "gitignore_subset_v1"
    )
    assert body["workspace_id"] == "a" * 32 and body["task_id"] == "b" * 32
    assert body["files"] == [
        {
            "relative_path": "safe.py",
            "file_type": "source",
            "language": "python",
            "byte_count": len((root / "safe.py").read_bytes()),
            "sha256": sha256((root / "safe.py").read_bytes()).hexdigest(),
        }
    ]
    assert body["scanned_directories"] == body["inspected_files"] == 1
    assert not body["truncated"] and body["incomplete_reasons"] == []
    assert body["excluded_counts"] == {"gitignore": 1, "hidden": 2}
    assert (
        str(root) not in response.text
        and "PRIVATE" not in response.text
        and TOKEN not in response.text
    )
    assert response.headers["cache-control"] == "no-store"
    assert snapshot(root) == before
    # 本机身份初始化可执行既有幂等users INSERT，不把它冒称零SQL写语句。
    assert statements and all(
        sql.startswith("select")
        or ("insert into users" in sql and "on conflict" in sql)
        for sql in statements
    )


@pytest.mark.parametrize(
    "kind", ["missing-task", "wrong-project", "foreign-owner", "foreign-conversation"]
)
def test_authorization_before_any_filesystem(api, target, engine, monkeypatch, kind):
    path = api[1]
    with Session(engine) as session, session.begin():
        if kind == "missing-task":
            path = path.replace(target["task_id"], "f" * 32)
        elif kind == "wrong-project":
            session.add(
                Workspace(external_id="f" * 32, name="other", user_id=target["user_id"])
            )
            path = path.replace(target["workspace_id"], "f" * 32)
        elif kind == "foreign-owner":
            require_value(session.scalar(select(Workspace))).user_id = target[
                "other_id"
            ]
        else:
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
    monkeypatch.setattr(
        workspace_path,
        "_resolve_bound_path",
        lambda **kwargs: pytest.fail("unauthorized filesystem access"),
    )
    response = api[0].get(path, headers=HEADERS)
    assert (
        response.status_code == 404
        and response.json()["code"] == "workspace_not_accessible"
    )


def test_unbound_does_not_fall_back_to_cwd(api, engine, monkeypatch):
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(Workspace))).root_path = None
    monkeypatch.setattr(
        workspace_path,
        "_resolve_bound_path",
        lambda **kwargs: pytest.fail("unbound filesystem access"),
    )
    response = get(api)
    assert (
        response.status_code == 422
        and response.json()["code"] == "workspace_directory_unbound"
    )


@pytest.mark.parametrize("change", ["binding", "workspace-owner", "conversation-owner"])
def test_reauthorization_between_listing_and_rule_read_rejects_before_new_io(
    api, root, engine, target, monkeypatch, change
):
    original = service.list_task_directory

    def listing(**kwargs):
        result = original(**kwargs)
        with Session(engine) as session, session.begin():
            if change == "binding":
                replacement = root.parent / "new-root"
                replacement.mkdir()
                (replacement / ".gitignore").write_text("PRIVATE")
                require_value(session.scalar(select(Workspace))).root_path = str(
                    replacement
                )
            elif change == "workspace-owner":
                require_value(session.scalar(select(Workspace))).user_id = target[
                    "other_id"
                ]
            else:
                require_value(
                    session.get(Conversation, target["conversation_pk"])
                ).user_id = target["other_id"]
        return result

    monkeypatch.setattr(service, "list_task_directory", listing)
    monkeypatch.setattr(
        workspace_file,
        "_read_resolved_file",
        lambda *args: pytest.fail("read after scope change"),
    )
    response = get(api)
    assert response.status_code == (409 if change == "binding" else 404)
    assert response.json()["code"] == (
        "workspace_directory_changed"
        if change == "binding"
        else "workspace_not_accessible"
    )
    assert "files" not in response.json() and "PRIVATE" not in response.text


def test_final_rule_check_rejects_change_after_actual_source_read(
    api, root, monkeypatch
):
    original = service.read_task_text_file

    def read(**kwargs):
        result = original(**kwargs)
        if kwargs["relative_path"] == "safe.py":
            (root / ".gitignore").write_text("safe.py\n")
        return result

    monkeypatch.setattr(service, "read_task_text_file", read)
    response = get(api)
    assert (
        response.status_code == 409 and response.json()["code"] == "code_ignore_changed"
    )
    assert "files" not in response.json() and "SOURCE_PRIVATE" not in response.text


@pytest.mark.parametrize("path", ["linked.py", "linked-dir"])
def test_links_are_excluded_without_target_reads(api, root, monkeypatch, path):
    external = root.parent / "outside"
    external.mkdir()
    (external / "private.py").write_text("PRIVATE")
    link = root / path
    link.symlink_to(
        external if path == "linked-dir" else external / "private.py",
        target_is_directory=path == "linked-dir",
    )
    original = workspace_file._read_resolved_file

    def read(file):
        assert file.is_relative_to(root)
        assert file.name != path
        return original(file)

    monkeypatch.setattr(workspace_file, "_read_resolved_file", read)
    response = get(api)
    assert response.status_code == 200 and [
        file["relative_path"] for file in response.json()["files"]
    ] == ["safe.py"]


def test_source_replaced_with_link_after_resolution_is_rejected(api, root, monkeypatch):
    original = workspace_path._resolve_bound_path

    def resolve(**kwargs):
        path = original(**kwargs)
        if path == root / "safe.py":
            path.unlink()
            path.symlink_to(root / ".env")
        return path

    monkeypatch.setattr(workspace_path, "_resolve_bound_path", resolve)
    response = get(api)
    assert response.status_code == 422 and response.json()["code"] == "file_not_regular"
    assert "PRIVATE" not in response.text


@pytest.mark.parametrize(
    "data,code",
    [
        (b"[", "unsupported_gitignore"),
        (b"x" * 16385, "unsupported_gitignore"),
        (b"\xff", "file_not_utf8_text"),
    ],
)
def test_bad_rules_never_return_partial_or_empty_success(api, root, data, code):
    (root / ".gitignore").write_bytes(data)
    response = get(api)
    assert response.status_code == 422 and response.json()["code"] == code
    assert (
        response.headers["cache-control"] == "no-store"
        and "files" not in response.json()
    )


@pytest.mark.parametrize(
    "error,status,code",
    [
        (WorkspaceFileError("file_changed", "读取时变化"), 409, "file_changed"),
        (
            WorkspaceFileError("file_access_denied", "没有权限"),
            403,
            "file_access_denied",
        ),
        (
            WorkspaceFileError("file_read_unsupported", "平台不支持"),
            501,
            "file_read_unsupported",
        ),
        (RuntimeError("PRIVATE /host/path"), 500, "code_inventory_failed"),
    ],
)
def test_read_failures_are_safe_and_not_empty_success(
    api, monkeypatch, error, status, code
):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(service, "read_task_text_file", fail)
    response = get(api)
    assert response.status_code == status and response.json()["code"] == code
    assert "files" not in response.json() and "PRIVATE" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        "root=/private",
        "user_id=1",
        "limit=999",
        "path=safe.py",
        "rules=!*",
        "limit=1&limit=2",
    ],
)
def test_no_client_scope_or_budget_overrides(api, monkeypatch, query):
    monkeypatch.setattr(
        route,
        "scan_code_inventory",
        lambda **kwargs: pytest.fail("invalid query reached scanner"),
    )
    response = api[0].get(api[1] + "?" + query, headers=HEADERS)
    assert (
        response.status_code == 422
        and response.json()["code"] == "invalid_code_inventory_input"
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        HEADERS | {"X-Local-Runtime-Token": "b" * 64},
        HEADERS | {"Host": "evil.test"},
        HEADERS | {"Origin": "https://evil.test"},
    ],
)
def test_local_access_before_scanner(api, monkeypatch, headers):
    monkeypatch.setattr(
        route,
        "scan_code_inventory",
        lambda **kwargs: pytest.fail("boundary reached scanner"),
    )
    assert get(api, headers=headers).status_code == 403


def test_account_mode_rejected_before_identity(api, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "account")
    monkeypatch.setattr(
        dependencies,
        "SessionLocal",
        lambda: pytest.fail("mode should reject before identity"),
    )
    assert get(api).json()["code"] == "local_mode_required"


def test_invalid_identifier_uses_safe_existing_boundary(api):
    response = api[0].get(api[1].replace("b" * 32, "PRIVATE"), headers=HEADERS)
    assert response.status_code == 422 and "PRIVATE" not in response.text
