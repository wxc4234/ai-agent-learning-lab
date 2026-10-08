"""注册API的严格输入、公开投影与固定错误；本文件不创建数据库。"""

import asyncio
from dataclasses import replace
from hashlib import sha256
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from starlette.requests import Request

from app import dependencies
from app.config import settings
from app.local_boundary import local_access_boundary
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.routers.workspace import code_query_context as route
from app.routers.workspace.router import router
from app.services.auth.authentication_service import AuthenticatedUser
from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.files.code_context import (
    CodeContextError,
    build_code_context,
)
from app.services.workspace.files.code_query_context import CodeQueryContextResult
from app.services.workspace.files.code_vector_search import CodeVectorSearchError
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_context import recall
from tests.workspace.files.test_code_inventory_api import HEADERS, TOKEN


URL = "/workspaces/" + "a" * 32 + "/tasks/" + "b" * 32 + "/code-query-context"
BODY = {
    "query": "  查找任务取消\n",
    "batch_id": "c" * 32,
    "response_model": "fixture-model-v1",
}


def result():
    return CodeQueryContextResult(
        query_sha256=sha256(BODY["query"].encode()).hexdigest(),
        requested_model="fixture-model",
        response_model="fixture-model-v1",
        prompt_tokens=None,
        total_tokens=None,
        request_count=1,
        context=build_code_context(recall()),
    )


def forbidden(*args, **kwargs):
    pytest.fail("rejected request reached configuration or model service")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_runtime_token", SecretStr(TOKEN))
    monkeypatch.setattr(route, "load_embedding_config", config)
    monkeypatch.setattr(dependencies, "SessionLocal", forbidden)
    app = FastAPI()
    app.middleware("http")(local_access_boundary)
    app.include_router(router)
    app.dependency_overrides[dependencies.require_current_user] = lambda: (
        AuthenticatedUser(7, "local", "")
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


def post(api, body=None, **kwargs):
    return api.post(
        URL,
        json=BODY if body is None else body,
        headers=kwargs.pop("headers", HEADERS),
        **kwargs,
    )


def assert_failure(response, status, code):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == code
    assert "PRIVATE" not in response.text and "context" not in response.json()


@pytest.mark.parametrize("usage", [(None, None), (0, 0), (7, 9)])
def test_registered_success_exact_arguments_public_projection_and_separate_usage(
    api, monkeypatch, usage
):
    calls = []
    original = result()

    async def build(query, **kwargs):
        calls.append((query, kwargs))
        return replace(original, prompt_tokens=usage[0], total_tokens=usage[1])

    monkeypatch.setattr(route, "build_code_query_context", build)
    response = post(api)
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    query, arguments = calls[0]
    assert query == BODY["query"] and len(calls) == 1
    assert set(arguments) == {
        "user_id",
        "workspace_id",
        "task_id",
        "batch_id",
        "config",
        "response_model",
        "top_k",
        "budget",
    }
    assert arguments["user_id"] == 7
    assert arguments["workspace_id"] == "a" * 32 and arguments["task_id"] == "b" * 32
    assert arguments["batch_id"] == BODY["batch_id"]
    assert arguments["config"] == config() and arguments["top_k"] == 5
    assert arguments["response_model"] == BODY["response_model"]
    assert arguments["budget"] == route.CodeContextBudget()
    body = response.json()
    assert set(body) == {
        "query_sha256",
        "requested_model",
        "response_model",
        "prompt_tokens",
        "total_tokens",
        "request_count",
        "context",
        "query_source",
        "source",
    }
    assert (body["prompt_tokens"], body["total_tokens"]) == usage
    assert body["request_count"] == 1 and body["query_source"] == "query_embedding"
    assert body["source"] == "code_query_context"
    context = body["context"]
    assert context["context_text"] == original.context.context_text
    assert context["selected_chunks"] == list(original.context.selected_chunks)
    assert context["source_metadata"]["prompt_tokens"] is None
    assert context["source_metadata"]["request_count"] == 1
    assert context["budget"] == {
        "max_chunks": 5,
        "max_chars": 12000,
        "max_bytes": 24000,
    }
    assert "PRIVATE" not in response.text and "provider.invalid" not in response.text
    for field in ("query", "vector", "api_key", "base_url", "root_path", "user_id"):
        assert f'"{field}"' not in response.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("query", None),
        ("query", True),
        ("query", 7),
        ("query", []),
        ("query", ""),
        ("query", " \n"),
        ("query", "PRIVATE\x00"),
        ("query", "\ud800"),
        ("query", "x" * 2001),
        ("query", "中" * 1366),
        ("batch_id", None),
        ("batch_id", 7),
        ("batch_id", "C" * 32),
        ("batch_id", "PRIVATE"),
        ("batch_id", "c" * 32 + "\n"),
        ("response_model", None),
        ("response_model", True),
        ("response_model", ""),
        ("response_model", " PRIVATE "),
        ("response_model", "PRIVATE\n"),
        ("response_model", "\ud800"),
        ("response_model", "x" * 257),
    ],
)
def test_invalid_body_rejected_before_config_or_service(api, monkeypatch, field, value):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    monkeypatch.setattr(route, "build_code_query_context", forbidden)
    # ensure_ascii使无效代理项也抵达真实JSON解析器，而不是HTTPX编码前失败。
    response = api.post(
        URL,
        content=json.dumps(BODY | {field: value}),
        headers=HEADERS | {"Content-Type": "application/json"},
    )
    assert_failure(response, 422, "invalid_code_query_context_input")


@pytest.mark.parametrize("field", ["query", "batch_id", "response_model"])
def test_missing_fields_never_request(api, monkeypatch, field):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    assert_failure(
        post(api, {key: value for key, value in BODY.items() if key != field}),
        422,
        "invalid_code_query_context_input",
    )


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "workspace_id",
        "task_id",
        "root_path",
        "config",
        "api_key",
        "base_url",
        "top_k",
        "budget",
        "transport",
    ],
)
def test_no_client_identity_model_or_budget_override(api, monkeypatch, field):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    assert_failure(
        post(api, BODY | {field: "PRIVATE"}), 422, "invalid_code_query_context_input"
    )


@pytest.mark.parametrize("raw", ["null", "[]", '"PRIVATE"', '{"query":', "{"])
def test_malformed_or_non_object_json_safe(api, monkeypatch, raw):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    response = api.post(
        URL, content=raw, headers=HEADERS | {"Content-Type": "application/json"}
    )
    assert_failure(response, 422, "invalid_code_query_context_input")


@pytest.mark.parametrize("field", ["query", "batch_id", "response_model"])
def test_duplicate_keys_rejected_before_config(api, monkeypatch, field):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    raw = (
        json.dumps(BODY)[:-1]
        + ","
        + json.dumps(field)
        + ":"
        + json.dumps(BODY[field])
        + "}"
    )
    response = api.post(
        URL, content=raw, headers=HEADERS | {"Content-Type": "application/json"}
    )
    assert_failure(response, 422, "invalid_code_query_context_input")


def test_query_parameters_and_invalid_path_are_sanitized(api, monkeypatch):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    assert_failure(
        post(api, params={"user_id": "PRIVATE"}),
        422,
        "invalid_code_query_context_input",
    )
    response = api.post(URL.replace("b" * 32, "PRIVATE"), json=BODY, headers=HEADERS)
    assert_failure(response, 422, "invalid_code_query_context_input")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        HEADERS | {"X-Local-Runtime-Token": "b" * 64},
        HEADERS | {"Host": "evil.test"},
        HEADERS | {"Origin": "https://evil.test"},
    ],
)
def test_local_access_denied_before_dependencies(api, monkeypatch, headers):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    assert_failure(post(api, headers=headers), 403, "local_access_rejected")


def test_account_mode_rejected_before_identity(api, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "account")
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    assert_failure(post(api), 403, "local_mode_required")


@pytest.mark.parametrize(
    "headers,status,code",
    [
        (
            {"X-Local-Runtime-Token": TOKEN, "Content-Type": "application/json"},
            403,
            "workspace_origin_rejected",
        ),
        (
            HEADERS | {"Content-Type": "text/plain"},
            415,
            "unsupported_workspace_content_type",
        ),
    ],
)
def test_origin_and_content_type_boundaries(api, monkeypatch, headers, status, code):
    monkeypatch.setattr(route, "load_embedding_config", forbidden)
    assert_failure(post(api, headers=headers), status, code)


@pytest.mark.parametrize(
    "code,status",
    [
        ("embedding_not_configured", 503),
        ("embedding_config_invalid", 503),
        ("embedding_query_invalid", 422),
        ("embedding_query_budget_exceeded", 422),
        ("embedding_timeout", 504),
        ("embedding_request_failed", 502),
        ("embedding_response_invalid", 502),
        ("embedding_response_too_large", 502),
        ("code_embedding_query_invalid", 422),
        ("code_embedding_query_zero", 502),
        ("code_embedding_query_result_invalid", 409),
        ("code_embedding_batch_inconsistent", 409),
        ("code_embedding_distance_invalid", 500),
        ("code_context_budget_invalid", 500),
        ("code_context_budget_too_small", 409),
        ("code_context_query_result_invalid", 500),
        ("code_context_snapshot_invalid", 500),
        ("code_context_snapshot_too_large", 500),
    ],
)
def test_known_errors_have_fixed_status_and_message(api, monkeypatch, code, status):
    error = (
        EmbeddingError(code, "PRIVATE Key=/private/path provider-body")
        if code.startswith("embedding_")
        else CodeContextError(code)
        if code.startswith("code_context_")
        else CodeVectorSearchError(code)
    )

    async def build(*args, **kwargs):
        raise error

    monkeypatch.setattr(route, "build_code_query_context", build)
    response = post(api)
    assert_failure(response, status, code)
    assert response.json()["message"] and response.json()["message"] != str(error)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("PRIVATE /private/path"),
        EmbeddingError("PRIVATE_UNKNOWN", "PRIVATE"),
        CodeContextError("PRIVATE_UNKNOWN"),
        CodeVectorSearchError("PRIVATE_UNKNOWN"),
    ],
)
def test_unknown_failures_never_return_raw_error_or_empty_success(
    api, monkeypatch, error
):
    async def build(*args, **kwargs):
        raise error

    monkeypatch.setattr(route, "build_code_query_context", build)
    assert_failure(post(api), 500, "code_query_context_failed")


def test_authorization_error_uses_existing_uniform_404(api, monkeypatch):
    async def build(*args, **kwargs):
        raise WorkspaceNotAccessibleError()

    monkeypatch.setattr(route, "build_code_query_context", build)
    assert_failure(post(api), 404, "workspace_not_accessible")


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "query",
        "model",
        "version",
        "batch",
        "scope",
        "extra-metadata",
        "extra-chunk",
        "nonfinite",
    ],
)
def test_unknown_mismatched_or_private_result_is_not_published(api, monkeypatch, kind):
    value = result()
    if kind == "unknown":
        value = {"PRIVATE": "unknown object"}
    elif kind in {"query", "model", "version"}:
        value = replace(
            value,
            **{
                {
                    "query": "query_sha256",
                    "model": "requested_model",
                    "version": "response_model",
                }[kind]: "d" * 64 if kind == "query" else "PRIVATE"
            },
        )
    elif kind == "batch":
        value = replace(value, context=replace(value.context, batch_id="d" * 32))
    elif kind in {"scope", "extra-metadata"}:
        value.context.source_metadata["task_id" if kind == "scope" else "api_key"] = (
            "PRIVATE"
        )
    elif kind == "extra-chunk":
        value.context.selected_chunks[0]["chunk"]["api_key"] = "PRIVATE"
    else:
        value.context.selected_chunks[0]["distance"] = float("nan")

    async def build(*args, **kwargs):
        return value

    monkeypatch.setattr(route, "build_code_query_context", build)
    assert_failure(post(api), 500, "code_query_context_failed")


def test_external_cancel_is_not_converted_to_business_response(monkeypatch):
    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(BODY).encode(),
            "more_body": False,
        }

    async def build(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(route, "load_embedding_config", config)
    monkeypatch.setattr(route, "build_code_query_context", build)
    request = Request({"type": "http", "query_string": b""}, receive)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            route.query_context(
                "a" * 32,
                "b" * 32,
                route.CodeQueryContextRequest(**BODY),
                request,
                AuthenticatedUser(7, "local", ""),
            )
        )
