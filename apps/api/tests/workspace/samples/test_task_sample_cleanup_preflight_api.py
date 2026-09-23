"""真实 local GET、隔离 PostgreSQL 与自有目录验证清理待办诊断 HTTP 边界。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.main import app
from app.models import User, Workspace, WorkspaceSampleOrigin
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.workspace.samples import task_sample_binding as binding_module
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError
from tests.local.test_local_mode import HEADERS, local_client
from tests.workspace.samples.test_task_sample_binding import setup, target

__all__ = ["local_client", "setup", "target"]


@pytest.fixture
def endpoint(local_client, setup, engine):
    bindings, scope, _, _ = setup
    with Session(engine) as session, session.begin():
        session.get(User, scope["user_id"]).external_id = LOCAL_USER_ID
    app.dependency_overrides[get_sample_bindings] = lambda: bindings
    try:
        url = (
            f"/workspaces/{scope['workspace_id']}/tasks/{scope['task_id']}"
            "/sample-cleanup-preflight"
        )
        yield local_client, url
    finally:
        app.dependency_overrides.pop(get_sample_bindings, None)


def read(endpoint, **kwargs):
    client, url = endpoint
    return client.get(url, **({"headers": HEADERS} | kwargs))


def safe(response, expected, code=None):
    assert response.status_code == expected, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert "PRIVATE" not in response.text
    payload = response.json()
    assert not {"root_path", "path", "parent_dev", "parent_ino", "root_dev", "root_ino", "handle", "token"} & payload.keys()
    if code is not None:
        assert payload == {"code": code, "message": payload["message"]}
    return payload


def evidence(engine):
    with Session(engine) as session:
        workspace = session.scalar(select(Workspace))
        origin = session.get(WorkspaceSampleOrigin, workspace.id)
        return (
            workspace.root_path,
            None if origin is None else (
                origin.workspace_id,
                origin.task_id,
                origin.root_path,
                origin.lifecycle_state,
                origin.parent_dev,
                origin.parent_ino,
                origin.root_dev,
                origin.root_ino,
            ),
        )


def leave_pending(bindings, scope, engine, monkeypatch):
    """只延迟本次物理清理；测试夹具退出时仍清理自有目录。"""
    bindings.bind(**scope)
    path = Path(evidence(engine)[1][2])
    with monkeypatch.context() as patch:
        patch.setattr(bindings._registry, "close", lambda _handle: False)
        with pytest.raises(TaskSampleBindingError) as caught:
            bindings.close(**scope)
    assert caught.value.code == "sample_cleanup_incomplete"
    return path


@pytest.mark.parametrize("result", [
    "evidence_missing",
    "not_pending",
    "evidence_inconsistent",
    "directory_missing",
    "identity_unverifiable",
    "identity_matches_record",
    "inspection_unavailable",
])
def test_fixed_results_project_only_public_fields(endpoint, setup, monkeypatch, result):
    bindings, scope, _, _ = setup
    monkeypatch.setattr(
        bindings,
        "read_cleanup_preflight",
        lambda **_kwargs: SimpleNamespace(result=result, root_path="PRIVATE"),
    )

    response = read(endpoint)

    assert safe(response, 200) == {
        "workspace_id": scope["workspace_id"],
        "task_id": scope["task_id"],
        "result": result,
    }


@pytest.mark.parametrize("bound,expected", [
    (False, "evidence_missing"),
    (True, "not_pending"),
])
def test_real_missing_and_active_are_read_only(endpoint, setup, engine, bound, expected):
    bindings, scope, _, _ = setup
    if bound:
        bindings.bind(**scope)
    before = evidence(engine)

    payload = safe(read(endpoint), 200)

    assert payload == {
        "workspace_id": scope["workspace_id"],
        "task_id": scope["task_id"],
        "result": expected,
    }
    assert evidence(engine) == before


@pytest.mark.parametrize("legacy", [False, True])
def test_real_pending_read_only_snapshot(endpoint, setup, engine, monkeypatch, legacy):
    bindings, scope, tracked, sessions = setup
    path = leave_pending(bindings, scope, engine, monkeypatch)
    if legacy:
        with Session(engine) as session, session.begin():
            origin = session.scalar(select(WorkspaceSampleOrigin))
            origin.parent_dev = None
            origin.parent_ino = None
            origin.root_dev = None
            origin.root_ino = None
    before = evidence(engine)
    sample_file = path / "example.txt"
    content_before = sample_file.read_bytes()
    root_info = path.stat(follow_symlinks=False)
    file_info = sample_file.stat(follow_symlinks=False)

    def unexpected(*_args, **_kwargs):
        pytest.fail("read-only preflight must not create, borrow, close, or commit")

    with monkeypatch.context() as patch:
        for operation in ("create", "borrow", "close"):
            patch.setattr(bindings._registry, operation, unexpected)
        patch.setattr(tracked, "commit", unexpected)
        payload = safe(read(endpoint), 200)

    assert payload == {
        "workspace_id": scope["workspace_id"],
        "task_id": scope["task_id"],
        "result": "identity_unverifiable" if legacy else "identity_matches_record",
    }
    assert evidence(engine) == before
    assert sample_file.read_bytes() == content_before
    assert (path.stat(follow_symlinks=False).st_ino, path.stat(follow_symlinks=False).st_mtime_ns) == (
        root_info.st_ino, root_info.st_mtime_ns,
    )
    assert (sample_file.stat(follow_symlinks=False).st_ino, sample_file.stat(follow_symlinks=False).st_mtime_ns) == (
        file_info.st_ino, file_info.st_mtime_ns,
    )
    assert all(item.closed and not item.in_transaction() for item in sessions)


@pytest.mark.parametrize("change,expected", [
    ("identity", "evidence_inconsistent"),
    ("missing_directory", "directory_missing"),
])
def test_real_pending_identity_or_path_change_is_only_observed(
    endpoint, setup, engine, monkeypatch, change, expected,
):
    bindings, scope, _, _ = setup
    path = leave_pending(bindings, scope, engine, monkeypatch)
    held = path.with_name(path.name + ".held")
    if change == "identity":
        with Session(engine) as session, session.begin():
            session.scalar(select(WorkspaceSampleOrigin)).root_ino += 1
    else:
        path.rename(held)
    try:
        before = evidence(engine)
        payload = safe(read(endpoint), 200)
        assert payload == {
            "workspace_id": scope["workspace_id"],
            "task_id": scope["task_id"],
            "result": expected,
        }
        assert evidence(engine) == before
    finally:
        if change == "missing_directory":
            held.rename(path)
    assert path.is_dir()


@pytest.mark.parametrize("kind", ["workspace", "task", "foreign", "sibling"])
def test_only_owned_source_task_can_read_classification(
    endpoint, setup, engine, target, monkeypatch, kind,
):
    bindings, scope, _, _ = setup
    bindings.bind(**scope)
    before = evidence(engine)
    client, url = endpoint
    if kind in ("workspace", "task"):
        url = url.replace(scope[kind + "_id"], "f" * 32)
    elif kind == "sibling":
        url = url.replace(scope["task_id"], "d" * 32)
    else:
        # 本机身份读取另一用户的项目，不能得到来源或目录分类。
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).user_id = target["other_id"]
        before = evidence(engine)

    with monkeypatch.context() as patch:
        patch.setattr(
            binding_module,
            "inspect_pending_sample_directory",
            lambda *_args, **_kwargs: pytest.fail("inaccessible task must not inspect files"),
        )
        response = client.get(url, headers=HEADERS)

    assert safe(response, 404, "workspace_not_accessible")
    assert evidence(engine) == before


@pytest.mark.parametrize("kind", ["query", "body", "workspace", "task"])
def test_invalid_input_is_rejected_before_service(endpoint, setup, monkeypatch, kind):
    bindings, scope, _, _ = setup
    client, url = endpoint
    monkeypatch.setattr(
        bindings,
        "read_cleanup_preflight",
        lambda **_kwargs: pytest.fail("invalid input must not read preflight"),
    )
    kwargs = {}
    if kind == "query":
        url += "?root_path=PRIVATE"
    elif kind == "body":
        kwargs["content"] = b'{"root_path":"PRIVATE"}'
    else:
        url = url.replace(scope[kind + "_id"], "INVALID")

    response = client.request("GET", url, headers=HEADERS, **kwargs)

    assert safe(response, 422, "invalid_sample_cleanup_preflight_input")


@pytest.mark.parametrize("kind", ["token", "host", "origin", "mode"])
def test_local_boundary_rejects_before_service(endpoint, setup, monkeypatch, kind):
    bindings, _, _, _ = setup
    headers = dict(HEADERS)
    if kind == "mode":
        monkeypatch.setattr(settings, "app_mode", "account")
    else:
        headers[{"token": "X-Local-Runtime-Token", "host": "Host", "origin": "Origin"}[kind]] = "PRIVATE"
    monkeypatch.setattr(
        bindings,
        "read_cleanup_preflight",
        lambda **_kwargs: pytest.fail("local boundary must run before preflight"),
    )

    assert safe(read(endpoint, headers=headers), 403)


def test_origin_can_be_absent_but_internal_token_is_required(endpoint):
    headers = {key: value for key, value in HEADERS.items() if key != "Origin"}
    assert safe(read(endpoint, headers=headers), 200)["result"] == "evidence_missing"


@pytest.mark.parametrize("kind", ["database", "internal"])
def test_read_failure_has_fixed_private_response(endpoint, setup, monkeypatch, kind):
    bindings, _, _, _ = setup
    if kind == "database":
        monkeypatch.setattr(
            binding_module,
            "SessionLocal",
            lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE database path")),
        )
    else:
        monkeypatch.setattr(
            bindings,
            "read_cleanup_preflight",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("PRIVATE directory")),
        )

    assert safe(read(endpoint), 500, "sample_cleanup_preflight_read_failed")


@pytest.mark.parametrize("result", ["PRIVATE", "ready", None])
def test_invalid_internal_result_fails_closed(endpoint, setup, monkeypatch, result):
    bindings, _, _, _ = setup
    monkeypatch.setattr(
        bindings,
        "read_cleanup_preflight",
        lambda **_kwargs: SimpleNamespace(result=result, root_path="PRIVATE"),
    )

    assert safe(read(endpoint), 500, "sample_cleanup_preflight_read_failed")


def test_missing_evidence_does_not_touch_registry(endpoint, setup, monkeypatch):
    bindings, _, _, _ = setup

    def unexpected(*_args, **_kwargs):
        pytest.fail("preflight must not create, borrow, or close registrations")

    for operation in ("create", "borrow", "close"):
        monkeypatch.setattr(bindings._registry, operation, unexpected)

    assert safe(read(endpoint), 200)["result"] == "evidence_missing"
