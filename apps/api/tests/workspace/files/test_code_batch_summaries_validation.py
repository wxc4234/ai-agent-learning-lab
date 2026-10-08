"""批次列表的纯输入/响应与注册HTTP边界；本文件不创建数据库。"""

from datetime import datetime, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
import pytest

from app import dependencies
from app.config import settings
from app.local_boundary import local_access_boundary
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.routers.workspace import code_batches as route
from app.routers.workspace.router import router
from app.services.auth.authentication_service import AuthenticatedUser
from app.services.auth.login_session_resolver import InvalidLoginSessionError
from app.services.workspace.files import code_batch_summaries as service
from tests.workspace.files.test_code_inventory_api import HEADERS, TOKEN


URL = "/workspaces/" + "a" * 32 + "/tasks/" + "b" * 32 + "/code-embedding-batches"


def result():
    return service.CodeEmbeddingBatchList(
        workspace_id="a" * 32, task_id="b" * 32, has_more=False,
        batches=(service.CodeEmbeddingBatchSummary(
            batch_id="c" * 32, space_id="d" * 64, requested_model="fixture-model",
            response_model="fixture-model-v1", dimensions=3, chunk_count=2,
            truncated=True, incomplete_reasons=("chunk_budget",),
            created_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
        ),),
    )


def forbidden(*args, **kwargs):
    pytest.fail("rejected request reached identity/database or batch read")


def failure(response, status, code):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == code
    assert "PRIVATE" not in response.text


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_runtime_token", SecretStr(TOKEN))
    monkeypatch.setattr(dependencies, "SessionLocal", forbidden)
    app = FastAPI()
    app.middleware("http")(local_access_boundary)
    app.include_router(router)
    app.dependency_overrides[dependencies.require_current_user] = lambda: AuthenticatedUser(7, "local", "")
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, app


@pytest.mark.parametrize("field,value", [
    ("workspace_id", None), ("workspace_id", "a" * 31), ("workspace_id", "A" * 32),
    ("workspace_id", "a" * 32 + "\n"), ("task_id", "b" * 33), ("task_id", 7),
    ("user_id", True), ("user_id", 0), ("user_id", 1.0), ("user_id", 2**31),
])
def test_invalid_service_scope_never_opens_session(monkeypatch, field, value):
    monkeypatch.setattr(service, "SessionLocal", SimpleNamespace(begin=forbidden))
    args = {"user_id": 7, "workspace_id": "a" * 32, "task_id": "b" * 32}
    args[field] = value
    with pytest.raises(service.CodeBatchSummaryError, match="invalid_code_embedding_batch_list_input"):
        service.list_code_embedding_batches(**args)


@pytest.mark.parametrize("changes", [
    {"dimensions": True}, {"dimensions": 0}, {"dimensions": 4097}, {"chunk_count": 21},
    {"chunk_count": "2"}, {"batch_id": "PRIVATE"}, {"space_id": "g" * 64},
    {"requested_model": " model"}, {"response_model": "model\n"},
    {"requested_model": "中" * 257}, {"truncated": False},
    {"incomplete_reasons": ["unknown"]}, {"incomplete_reasons": ["chunk_budget", "chunk_budget"]},
    {"created_at": "2026-10-08T00:00:00"}, {"private_root": "/PRIVATE"},
])
def test_summary_schema_rejects_invalid_public_fields(changes):
    payload = result().model_dump(mode="json")["batches"][0]
    with pytest.raises(ValidationError):
        service.CodeEmbeddingBatchSummary.model_validate_json(json.dumps(payload | changes))


@pytest.mark.parametrize("kind", ["duplicate", "order", "has_more_empty", "extra", "wrong_source"])
def test_list_schema_rejects_inconsistent_public_shape(kind):
    payload = result().model_dump(mode="json")
    item = payload["batches"][0]
    if kind == "duplicate":
        payload["batches"].append(item)
    elif kind == "order":
        payload["batches"].append(item | {"batch_id": "f" * 32})
    elif kind == "has_more_empty":
        payload.update(batches=[], has_more=True)
    elif kind == "extra":
        payload["private_root"] = "/PRIVATE"
    else:
        payload["source"] = "PRIVATE"
    # 使用JSON模式，数组和ISO时间可合法进入严格响应Schema。
    with pytest.raises(ValidationError):
        service.CodeEmbeddingBatchList.model_validate_json(json.dumps(payload))


def test_registered_success_exact_scope_and_public_fields(api, monkeypatch):
    calls = []
    monkeypatch.setattr(route, "list_code_embedding_batches", lambda **kwargs: calls.append(kwargs) or result())
    response = api[0].get(URL, headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls == [{"user_id": 7, "workspace_id": "a" * 32, "task_id": "b" * 32}]
    assert response.json() == result().model_dump(mode="json")
    assert set(response.json()["batches"][0]) == {
        "batch_id", "space_id", "requested_model", "response_model", "dimensions",
        "chunk_count", "truncated", "incomplete_reasons", "created_at",
    }


@pytest.mark.parametrize("path", [
    URL.replace("a" * 32, "PRIVATE"), URL.replace("b" * 32, "b" * 31),
    URL.replace("b" * 32, "b" * 33), URL.replace("a" * 32, "A" * 32),
    URL.replace("b" * 32, "b" * 32 + "%0A"),
])
def test_bad_path_is_fixed_input_failure(api, monkeypatch, path):
    monkeypatch.setattr(route, "list_code_embedding_batches", forbidden)
    failure(api[0].get(path, headers=HEADERS), 422, "invalid_code_embedding_batch_list_input")


@pytest.mark.parametrize("suffix,body", [
    ("?limit=1", b""), ("?before=PRIVATE", b""), ("?task_id=PRIVATE", b""),
    ("?query=one&query=two", b""), ("", b"{}"), ("", b" "), ("", b"PRIVATE"),
])
def test_query_or_nonempty_body_rejected_before_identity(api, monkeypatch, suffix, body):
    api[1].dependency_overrides[dependencies.require_current_user] = lambda: forbidden()
    monkeypatch.setattr(route, "list_code_embedding_batches", forbidden)
    failure(api[0].request("GET", URL + suffix, content=body, headers=HEADERS), 422, "invalid_code_embedding_batch_list_input")


@pytest.mark.parametrize("headers", [
    {}, HEADERS | {"X-Local-Runtime-Token": "b" * 64},
    HEADERS | {"Host": "evil.test"}, HEADERS | {"Origin": "https://evil.test"},
])
def test_local_boundary_before_dependencies(api, monkeypatch, headers):
    api[1].dependency_overrides[dependencies.require_current_user] = lambda: forbidden()
    monkeypatch.setattr(route, "list_code_embedding_batches", forbidden)
    failure(api[0].get(URL, headers=headers), 403, "local_access_rejected")


def test_local_mode_required_before_identity(api, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "account")
    api[1].dependency_overrides[dependencies.require_current_user] = lambda: forbidden()
    failure(api[0].get(URL, headers=HEADERS), 403, "local_mode_required")


@pytest.mark.parametrize("kind,status,code", [
    ("identity", 401, "invalid_login_session"), ("authorization", 404, "workspace_not_accessible"),
    ("unbound", 409, "code_embedding_project_unbound"),
    ("input", 422, "invalid_code_embedding_batch_list_input"),
    ("database", 500, "code_embedding_batch_list_failed"),
    ("unknown_code", 500, "code_embedding_batch_list_failed"),
    ("dependency", 500, "code_embedding_batch_list_failed"),
])
def test_fixed_failures_cover_dependencies_and_service(api, monkeypatch, kind, status, code):
    def fail(**kwargs):
        if kind == "identity":
            raise InvalidLoginSessionError()
        if kind == "authorization":
            raise WorkspaceNotAccessibleError()
        if kind in {"unbound", "input", "unknown_code"}:
            raise service.CodeBatchSummaryError(code if kind != "unknown_code" else "PRIVATE")
        raise RuntimeError("PRIVATE SQL path Key")
    if kind in {"identity", "dependency"}:
        api[1].dependency_overrides[dependencies.require_current_user] = lambda: fail()
    else:
        monkeypatch.setattr(route, "list_code_embedding_batches", fail)
    failure(api[0].get(URL, headers=HEADERS), status, code)


@pytest.mark.parametrize("kind", ["unknown", "scope", "budget", "source", "nested", "serialization"])
def test_unknown_or_bypassed_success_never_published(api, monkeypatch, kind):
    value = result()
    if kind == "unknown":
        value = SimpleNamespace(batches=[])  # 类型未知不能变成空列表。
    elif kind == "serialization":
        monkeypatch.setattr(route, "JSONResponse", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("PRIVATE")))
    elif kind == "nested":
        item = value.batches[0].model_copy(update={"requested_model": "PRIVATE\n"})
        value = value.model_copy(update={"batches": (item,)})
    else:
        changes = {"scope": {"task_id": "f" * 32}, "budget": {"limit": 1}, "source": {"source": "PRIVATE"}}
        value = value.model_copy(update=changes[kind])
    monkeypatch.setattr(route, "list_code_embedding_batches", lambda **kwargs: value)
    failure(api[0].get(URL, headers=HEADERS), 500, "code_embedding_batch_list_failed")
