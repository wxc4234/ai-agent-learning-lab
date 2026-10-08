"""真实本地身份、隔离PostgreSQL和受控HTTPX的代码上下文API。"""

import asyncio
from dataclasses import replace
from hashlib import sha256
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.local_boundary import local_access_boundary
from app.models import (
    CodeEmbeddingBatch,
    CodeEmbeddingVector,
    Conversation,
    User,
    Workspace,
)
from app.repositories.workspace.workspace_repository import set_locked_workspace_root
from app.routers.workspace import code_query_context as route
from app.routers.workspace.router import router
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.model import query_embeddings
from app.services.workspace.files import code_query_context, code_vector_storage
from tests.assertions import require_value
from tests.model.test_code_embeddings import TrackedStream, config
from tests.workspace.files.test_code_inventory_api import HEADERS, TOKEN
from tests.workspace.files.test_code_query_context import (
    context,
    context_database,
    database,
    target,
)
from tests.model.test_query_embeddings import closed_clients
from tests.workspace.files.test_code_query_search import query_response
from tests.workspace.files.test_code_vector_search import source_vectors
from tests.workspace.files.test_code_vector_storage import counts, save
from tests.workspace.files.test_code_vector_storage_validation import split_batch
from tests.workspace.files.test_code_query_context_api_validation import (
    BODY,
    assert_failure,
    forbidden,
)


__all__ = ["closed_clients", "context", "context_database", "database", "target"]


@pytest.fixture
def model(monkeypatch, closed_clients):
    # 替换传输但保留真实HTTPX客户端/响应与生成器；绝不访问外部供应商。
    original_client = httpx.AsyncClient
    state = {"handler": lambda request: query_response(), "calls": []}

    async def handle(request):
        state["calls"].append(request)
        response = state["handler"](request)
        return await response if asyncio.iscoroutine(response) else response

    def client(*args, **kwargs):
        assert kwargs["transport"] is None  # 路由没有引入客户端可控transport。
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(query_embeddings.httpx, "AsyncClient", client)
    return state


@pytest.fixture
def api(context_database, context, target, monkeypatch, model):
    active = config()
    for field, value in {
        "local_runtime_token": SecretStr(TOKEN),
        "embedding_api_key": active.api_key,
        "embedding_base_url": active.base_url,
        "embedding_model": active.model,
        "embedding_dimensions": active.dimensions,
        "embedding_request_dimensions": active.request_dimensions,
        "embedding_timeout_seconds": active.timeout_seconds,
    }.items():
        monkeypatch.setattr(settings, field, value)
    monkeypatch.setattr(dependencies, "SessionLocal", code_vector_storage.SessionLocal)
    # 保留真实CurrentUser解析；将隔离夹具已有owner映射为本机固定身份。
    with Session(context_database) as session, session.begin():
        owner = require_value(session.get(User, target["user_id"]))
        owner.external_id = LOCAL_USER_ID
        owner.username = owner.password_hash = None
    app = FastAPI()
    app.middleware("http")(local_access_boundary)
    app.include_router(router)
    path = (
        f"/workspaces/{context.workspace_id}/tasks/{context.task_id}/code-query-context"
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, path


def post(api, saved, **changes):
    return api[0].post(
        api[1], json=BODY | {"batch_id": saved.batch_id} | changes, headers=HEADERS
    )


@pytest.mark.parametrize("usage", ["unknown", "zero", "reported"])
def test_real_auth_query_recall_build_projection_and_read_only_sql(
    api, context, context_database, model, monkeypatch, closed_clients, usage
):
    source = replace(
        source_vectors([(0, 1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 0)]),
        prompt_tokens=40,
        total_tokens=50,
        request_count=2,
    )
    saved = save(context, source)
    before = counts(context_database)
    statements = []
    original_builder = code_query_context.build_code_context

    def handler(request):
        assert context_database.pool.checkedout() == 0
        assert json.loads(request.content)["input"] == [BODY["query"]]
        assert request.headers["authorization"] == "Bearer EMBED_PRIVATE"
        payload = json.loads(query_response(usage=False).content)
        if usage != "unknown":
            payload["usage"] = {
                "prompt_tokens": 0 if usage == "zero" else 7,
                "total_tokens": 0 if usage == "zero" else 9,
            }
        return httpx.Response(200, json=payload)

    def build(value, **kwargs):
        assert context_database.pool.checkedout() == 0
        assert len(closed_clients) == 1 and closed_clients[0].is_closed
        return original_builder(value, **kwargs)

    def collect(connection, cursor, sql, parameters, execution_context, executemany):
        statements.append(sql.lower().lstrip())

    model["handler"] = handler
    monkeypatch.setattr(code_query_context, "build_code_context", build)
    event.listen(context_database, "before_cursor_execute", collect)
    try:
        response = post(api, saved)
    finally:
        event.remove(context_database, "before_cursor_execute", collect)
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    body = response.json()
    assert body["query_sha256"] == sha256(BODY["query"].encode()).hexdigest()
    assert (body["prompt_tokens"], body["total_tokens"]) == {
        "unknown": (None, None),
        "zero": (0, 0),
        "reported": (7, 9),
    }[usage]
    package = body["context"]
    assert package["batch_id"] == saved.batch_id
    assert (
        package["source_metadata"]["prompt_tokens"] == 40
        and package["source_metadata"]["total_tokens"] == 50
    )
    assert (
        package["source_metadata"]["request_count"] == 2 and body["request_count"] == 1
    )
    assert package["selected_hit_count"] == 3
    assert [entry["rank"] for entry in package["selected_chunks"]] == [1, 2, 3]
    assert package["recall_summary"]["excluded_zero_chunk_count"] == 1
    assert package["context_chars"] == len(package["context_text"]) <= 12000
    assert package["context_bytes"] == len(package["context_text"].encode()) <= 24000
    assert package["content_trust"] == "untrusted_project_content"
    assert json.loads(package["context_text"])["chunks"] == package["selected_chunks"]
    assert len(model["calls"]) == 1 and counts(context_database) == before
    assert any("<=>" in statement for statement in statements)
    # 既有身份依赖会执行users的ON CONFLICT DO NOTHING；不是索引写入。
    writes = [
        statement
        for statement in statements
        if statement.startswith(
            ("insert", "update", "delete", "create", "alter", "drop")
        )
    ]
    assert len(writes) == 1 and "insert into users" in writes[0]
    assert "on conflict" in writes[0] and "do nothing" in writes[0]
    assert (
        "PRIVATE" not in response.text
        and "/preserved" not in response.text
        and "provider.invalid" not in response.text
    )


@pytest.mark.parametrize("kind", ["partial", "zero", "builder-omission"])
def test_real_coverage_empty_and_builder_omissions(api, context, model, kind):
    original = split_batch()
    source = (
        replace(
            original,
            embeddings=original.embeddings[:1],
            truncated=True,
            incomplete_reasons=("chunk_budget",),
        )
        if kind == "partial"
        else source_vectors([(0, 0, 0)] * 2)
    )
    if kind == "builder-omission":
        # 只覆盖本次实际字符预算省略，保留整片；不向客户端开放预算覆盖。
        from tests.workspace.files.test_code_vector_storage_validation import batch

        source = batch(
            texts=[
                "def first(): return '" + "x" * 1950 + "'\n",
                "def second(): return '" + "x" * 1950 + "'\n",
                "def third(): return '" + "x" * 1950 + "'\n",
                "def fourth(): return '" + "x" * 1950 + "'\n",
                "def fifth(): return '" + "x" * 1950 + "'\n",
            ]
        )
    saved = save(context, source)
    response = post(api, saved)
    assert response.status_code == 200
    package = response.json()["context"]
    assert len(model["calls"]) == 1
    if kind == "partial":
        assert package["source_metadata"]["truncated"]
        assert package["source_metadata"]["incomplete_reasons"]
        assert package["selected_chunks"][0]["chunk"]["part_count"] == 2
    elif kind == "zero":
        assert package["selected_chunks"] == [] and package["selected_hit_count"] == 0
        assert package["recall_summary"]["excluded_zero_chunk_count"] == 2
        assert json.loads(package["context_text"])["chunks"] == []
    else:
        assert package["omissions"] and package["selected_hit_count"] < 5
        assert any(
            "character_budget" in omission["reasons"]
            for omission in package["omissions"]
        )


@pytest.mark.parametrize(
    "kind",
    ["workspace-owner", "conversation-owner", "task", "batch", "space", "binding"],
)
def test_initial_current_auth_or_space_denial_sends_nothing(
    api, context, context_database, target, model, monkeypatch, kind
):
    saved = save(context)
    changes = {}
    path = api[1]
    with Session(context_database) as session, session.begin():
        if kind == "workspace-owner":
            require_value(
                session.scalar(
                    select(Workspace).where(
                        Workspace.external_id == context.workspace_id
                    )
                )
            ).user_id = target["other_id"]
        elif kind == "conversation-owner":
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
        elif kind == "binding":
            set_locked_workspace_root(
                require_value(
                    session.scalar(
                        select(Workspace).where(
                            Workspace.external_id == context.workspace_id
                        )
                    )
                ),
                "/changed",
            )
        elif kind == "task":
            path = path.replace(context.task_id, "d" * 32)
        elif kind == "batch":
            changes = {"batch_id": "f" * 32}
        else:
            changes = {"response_model": "different-version"}
    monkeypatch.setattr(code_query_context, "build_code_context", forbidden)
    response = api[0].post(
        path, json=BODY | {"batch_id": saved.batch_id} | changes, headers=HEADERS
    )
    assert_failure(response, 404, "workspace_not_accessible")
    assert not model["calls"]


@pytest.mark.parametrize(
    "kind", ["workspace-owner", "conversation-owner", "root-roundtrip", "batch-delete"]
)
def test_revocation_committed_during_model_wait_denies_response(
    api, context, context_database, target, model, monkeypatch, kind
):
    saved = save(context)
    monkeypatch.setattr(code_query_context, "build_code_context", forbidden)

    async def handler(request):
        assert context_database.pool.checkedout() == 0
        await asyncio.sleep(0)
        # 独立连接真实提交；发送前授权不是等待后的继续读取许可。
        with Session(context_database) as session, session.begin():
            if kind == "workspace-owner":
                require_value(
                    session.scalar(
                        select(Workspace).where(
                            Workspace.external_id == context.workspace_id
                        )
                    )
                ).user_id = target["other_id"]
            elif kind == "conversation-owner":
                require_value(
                    session.get(Conversation, target["conversation_pk"])
                ).user_id = target["other_id"]
            elif kind == "root-roundtrip":
                workspace = require_value(
                    session.scalar(
                        select(Workspace).where(
                            Workspace.external_id == context.workspace_id
                        )
                    )
                )
                set_locked_workspace_root(workspace, "/elsewhere")
                session.flush()
                set_locked_workspace_root(workspace, "/preserved")
            else:
                session.delete(
                    require_value(
                        session.scalar(
                            select(CodeEmbeddingBatch).where(
                                CodeEmbeddingBatch.external_id == saved.batch_id
                            )
                        )
                    )
                )
        return query_response()

    model["handler"] = handler
    assert_failure(post(api, saved), 404, "workspace_not_accessible")
    assert len(model["calls"]) == 1


@pytest.mark.parametrize(
    "kind,status,code",
    [
        ("version", 409, "code_embedding_query_result_invalid"),
        ("provider-error", 502, "embedding_request_failed"),
        ("bad-response", 502, "embedding_response_invalid"),
        ("snapshot", 500, "code_context_snapshot_invalid"),
    ],
)
def test_real_model_or_post_recall_failure_is_sanitized(
    api, context, context_database, model, kind, status, code
):
    saved = save(context)
    if kind == "version":
        model["handler"] = lambda request: query_response(model="PRIVATE_VERSION")
    elif kind == "provider-error":
        model["handler"] = lambda request: httpx.Response(
            500, text="PRIVATE provider body Key=/private/path"
        )
    elif kind == "bad-response":
        model["handler"] = lambda request: httpx.Response(
            200, json={"PRIVATE": "invalid"}
        )
    else:
        with Session(context_database) as session, session.begin():
            row = require_value(
                session.scalar(
                    select(CodeEmbeddingVector)
                    .join(CodeEmbeddingBatch)
                    .where(CodeEmbeddingBatch.external_id == saved.batch_id)
                )
            )
            row.chunk_metadata = row.chunk_metadata | {"text_sha256": "f" * 64}
    assert_failure(post(api, saved), status, code)
    assert len(model["calls"]) == 1


@pytest.mark.parametrize("kind", ["missing", "invalid"])
def test_actual_delayed_config_rejects_without_model(
    api, context, model, monkeypatch, kind
):
    saved = save(context)
    monkeypatch.setattr(
        settings, "embedding_model", "" if kind == "missing" else " PRIVATE "
    )
    assert_failure(
        post(api, saved),
        503,
        "embedding_not_configured" if kind == "missing" else "embedding_config_invalid",
    )
    assert not model["calls"]


def test_actual_timeout_closes_model_and_never_builds(
    api, context, model, monkeypatch, closed_clients
):
    saved = save(context)
    stream = TrackedStream(b"{", delay=20)
    # 缩短可信测试配置的墙钟预算；外部请求不能覆盖timeout。
    monkeypatch.setattr(
        route,
        "load_embedding_config",
        lambda: config().model_copy(update={"timeout_seconds": 0.01}),
    )
    monkeypatch.setattr(code_query_context, "build_code_context", forbidden)
    model["handler"] = lambda request: httpx.Response(
        200, headers={"content-type": "application/json"}, stream=stream
    )
    assert_failure(post(api, saved), 504, "embedding_timeout")
    assert stream.closed and len(model["calls"]) == 1
    assert len(closed_clients) == 1 and closed_clients[0].is_closed


def test_real_bad_token_rejected_before_identity_or_model(
    api, context, monkeypatch, model
):
    saved = save(context)
    monkeypatch.setattr(dependencies, "SessionLocal", forbidden)
    response = api[0].post(
        api[1],
        json=BODY | {"batch_id": saved.batch_id},
        headers=HEADERS | {"X-Local-Runtime-Token": "b" * 64},
    )
    assert_failure(response, 403, "local_access_rejected")
    assert not model["calls"]
