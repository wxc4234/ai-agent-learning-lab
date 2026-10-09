"""真实PG选择→查询→上下文；HTTP受控，使用根夹具隔离库和真实提交。"""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import CodeEmbeddingBatch, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError, set_locked_workspace_root
from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.files import code_batch_summaries, code_query_context, code_retrieval_context as service, code_vector_storage
from app.services.workspace.files.code_context import CodeContextBudget, CodeContextError
from app.services.workspace.files.code_vector_search import CodeVectorSearchError
from tests.assertions import require_value
from tests.model.test_code_embeddings import TrackedStream, config
from tests.model.test_query_embeddings import closed_clients
from tests.workspace.files.test_code_batch_summaries import row, scope
from tests.workspace.files.test_code_context import assert_package
from tests.workspace.files.test_code_query_context import context_database
from tests.workspace.files.test_code_query_search import query_response
from tests.workspace.files.test_code_vector_search import source_vectors
from tests.workspace.files.test_code_vector_storage import context, counts, database, save, target
from tests.workspace.files.test_code_vector_storage_validation import batch

__all__ = ["closed_clients", "context", "context_database", "database", "target"]


@pytest.fixture
def retrieval_database(context_database, monkeypatch):
    # 三个短事务使用同一跟踪Session工厂，父夹具检查退出时无连接/事务泄漏。
    monkeypatch.setattr(code_batch_summaries, "SessionLocal", code_vector_storage.SessionLocal)
    return context_database


def invoke(context, handler=None, **changes):
    return asyncio.run(service.build_code_retrieval_context(
        "  查找取消\n", **scope(context),
        **({"config": config(), "transport": httpx.MockTransport(handler or (lambda request: query_response()))} | changes),
    ))


def forbidden(*args, **kwargs):
    pytest.fail("unexpected query generation or index write")


@pytest.mark.parametrize("usage", ["unknown", "zero", "reported"])
def test_success_three_transactions_usage_coverage_and_read_only(retrieval_database, context, closed_clients, monkeypatch, usage):
    source = replace(source_vectors([(1, 0, 0), (0, 1, 0)]), truncated=True, incomplete_reasons=("chunk_budget",), prompt_tokens=40, total_tokens=50)
    saved = save(context, source)
    before = counts(retrieval_database)
    commits, calls, statements = [], [], []
    original = code_query_context.build_code_context

    def committed(session):
        commits.append(1)

    def observe(conn, cursor, statement, parameters, ctx, many):
        statements.append(statement.lower().lstrip())

    def handler(request):
        assert commits == [1, 1] and retrieval_database.pool.checkedout() == 0
        calls.append(request)
        assert json.loads(request.content)["input"] == ["  查找取消\n"]
        payload = json.loads(query_response(usage=False).content)
        if usage != "unknown":
            payload["usage"] = {"prompt_tokens": 0 if usage == "zero" else 7, "total_tokens": 0 if usage == "zero" else 9}
        return httpx.Response(200, json=payload)

    def build(snapshot, *, budget):
        assert commits == [1, 1, 1] and retrieval_database.pool.checkedout() == 0
        assert len(closed_clients) == 1 and closed_clients[0].is_closed
        return original(snapshot, budget=budget)

    monkeypatch.setattr(code_query_context, "build_code_context", build)
    monkeypatch.setattr(code_vector_storage, "save_code_embedding_batch", forbidden)
    cls = code_vector_storage.SessionLocal.class_
    event.listen(cls, "after_commit", committed)
    event.listen(retrieval_database, "before_cursor_execute", observe)
    try:
        result = invoke(context, handler, top_k=2, budget=CodeContextBudget(max_chunks=1))
    finally:
        event.remove(cls, "after_commit", committed)
        event.remove(retrieval_database, "before_cursor_execute", observe)
    queried = require_value(result.query_context)
    assert result.status == "context_ready" and require_value(result.selection.selected).batch_id == saved.batch_id
    assert_package(queried.context)
    assert (queried.prompt_tokens, queried.total_tokens) == {"unknown": (None, None), "zero": (0, 0), "reported": (7, 9)}[usage]
    assert queried.context.source_metadata["prompt_tokens"] == 40
    assert queried.context.source_metadata["truncated"] is True
    assert queried.context.omissions[0].reasons == ("chunk_budget",)
    assert len(calls) == 1 and all(sql.startswith("select") for sql in statements)
    assert counts(retrieval_database) == before


@pytest.mark.parametrize("kind", ["empty", "other-space", "beyond-window"])
def test_not_found_never_constructs_http_client(retrieval_database, context, closed_clients, kind):
    if kind == "beyond-window":
        old = save(context)
        with Session(retrieval_database) as session, session.begin():
            row(session, old.batch_id).created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    if kind != "empty":
        active = config(model="other")
        for _ in range(20 if kind == "beyond-window" else 1):
            save(context, batch(active=active), active)
    result = invoke(context, forbidden)
    assert result.status == "not_found_in_window" and result.query_context is None
    assert result.selection.has_more is (kind == "beyond-window")
    assert closed_clients == []


def test_zero_vectors_are_successful_empty_context(retrieval_database, context):
    save(context, source_vectors([(0, 0, 0)]))
    result = invoke(context)
    assert result.status == "context_ready"
    queried = require_value(result.query_context)
    assert queried.context.selected_hit_count == 0 and queried.request_count == 1


@pytest.mark.parametrize("phase", ["after-selection", "during-http"])
@pytest.mark.parametrize("kind", ["owner", "roundtrip", "delete"])
def test_reauthorization_after_selection_and_http(retrieval_database, context, target, monkeypatch, phase, kind):
    save(context)
    calls = []

    def revoke():
        assert retrieval_database.pool.checkedout() == 0
        with Session(retrieval_database) as session, session.begin():
            workspace = require_value(session.scalar(select(Workspace).with_for_update()))
            if kind == "owner":
                workspace.user_id = target["other_id"]
            elif kind == "roundtrip":
                set_locked_workspace_root(workspace, "/other")
                set_locked_workspace_root(workspace, context.bound_root)
            else:
                session.delete(require_value(session.scalar(select(CodeEmbeddingBatch))))

    original = service.select_code_embedding_batch

    def select_batch(**kwargs):
        result = original(**kwargs)
        if phase == "after-selection":
            revoke()
        return result

    def handler(request):
        calls.append(request)
        if phase == "during-http":
            revoke()
        return query_response()

    monkeypatch.setattr(service, "select_code_embedding_batch", select_batch)
    with pytest.raises(WorkspaceNotAccessibleError):
        invoke(context, handler)
    assert len(calls) == (1 if phase == "during-http" else 0)


@pytest.mark.parametrize("kind", ["version", "http", "budget"])
def test_failure_does_not_fallback_or_retry(retrieval_database, context, kind):
    save(context)
    save(context)  # 存在另一兼容批次也不能在失败后自动回退。
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "http":
            return httpx.Response(500, json={"error": "controlled"})
        return query_response(model="new-version" if kind == "version" else "fixture-model-v1")

    error = {"version": CodeVectorSearchError, "http": EmbeddingError, "budget": CodeContextError}[kind]
    with pytest.raises(error):
        invoke(context, handler, budget=CodeContextBudget(max_bytes=1) if kind == "budget" else None)
    assert len(calls) == 1


@pytest.mark.parametrize("phase", [1, 2, 3])
def test_each_transaction_exit_failure(retrieval_database, context, phase):
    save(context)
    commits, calls = [], []

    def fail(session):
        commits.append(1)
        if len(commits) == phase:
            raise RuntimeError("controlled exit failure")

    def handler(request):
        calls.append(request)
        return query_response()

    cls = code_vector_storage.SessionLocal.class_
    event.listen(cls, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="controlled exit failure"):
            invoke(context, handler)
    finally:
        event.remove(cls, "before_commit", fail)
    assert len(calls) == (1 if phase == 3 else 0)


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_timeout_and_cancel_close_client(retrieval_database, context, closed_clients, mode):
    save(context)
    stream = TrackedStream(b"{", delay=20)

    async def scenario():
        # 使用合法配置下限1秒，走真实HTTP等待超时；不绕过新增配置预检。
        task = asyncio.create_task(service.build_code_retrieval_context(
            "query", **scope(context), config=config(timeout_seconds=1.0),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)),
        ))
        if mode == "cancel":
            await asyncio.wait_for(stream.started.wait(), timeout=2)
            assert retrieval_database.pool.checkedout() == 0
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(EmbeddingError) as caught:
                await task
            assert caught.value.code == "embedding_timeout"

    asyncio.run(scenario())
    assert stream.closed and len(closed_clients) == 1
