"""真实PG与受控HTTP的混合查询组合，验证模型等待和后置拒绝。"""

import asyncio
from dataclasses import replace
from hashlib import sha256
import json

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError, set_locked_workspace_root
from app.services.model.embedding_config import EmbeddingError
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.workspace.files import code_hybrid_query_context as service
from app.services.workspace.files import code_keyword_search, code_query_search, code_vector_search, code_vector_storage
from app.services.workspace.files.code_context import CodeContextBudget, CodeContextError
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.model.test_query_embeddings import closed_clients
from tests.workspace.files.test_code_context import assert_package
from tests.workspace.files.test_code_query_search import arguments, query_response
from tests.workspace.files.test_code_vector_search import source_vectors
from tests.workspace.files.test_code_vector_storage import context, counts, database, save, target

__all__ = ["closed_clients", "context", "database", "target"]


@pytest.fixture
def hybrid_database(database, monkeypatch):
    for module in (code_keyword_search, code_query_search, code_vector_search):
        monkeypatch.setattr(module, "SessionLocal", code_vector_storage.SessionLocal)
    return database


def invoke(context, saved, *, handler=None, threaded=False, **changes):
    args = arguments(context, saved, query="sample", **changes)
    async def run():
        threads = ExecutionThreads() if threaded else None
        try:
            return await service.build_code_hybrid_query_context(**args, execution_threads=threads,
                transport=httpx.MockTransport(handler or (lambda request: query_response())))
        finally:
            if threads is not None:
                await threads.wait_closed()
    return asyncio.run(run())


@pytest.mark.parametrize("threaded", [False, True])
@pytest.mark.parametrize("usage", ["unknown", "zero", "reported"])
def test_real_chain_closes_three_transactions_and_http_before_builder(hybrid_database, context, closed_clients, monkeypatch, threaded, usage):
    original = replace(source_vectors([(1, 0, 0), (0, 1, 0), (0, 0, 0)]), prompt_tokens=40, total_tokens=50)
    saved = save(context, original)
    before = counts(hybrid_database)
    commits, calls = [], []
    def committed(session):
        commits.append(1)
    async def handler(request):
        assert hybrid_database.pool.checkedout() == 0 and len(commits) == 1
        await asyncio.sleep(0)
        calls.append(json.loads(request.content))
        value = json.loads(query_response(usage=False).content)
        if usage != "unknown":
            value["usage"] = {"prompt_tokens": 0 if usage == "zero" else 7, "total_tokens": 0 if usage == "zero" else 9}
        return httpx.Response(200, json=value)
    build = service.build_code_hybrid_context
    def checked_builder(snapshot, *, budget):
        assert hybrid_database.pool.checkedout() == 0 and len(commits) == 3
        assert len(closed_clients) == 1 and closed_clients[0].is_closed
        return build(snapshot, budget=budget)
    monkeypatch.setattr(service, "build_code_hybrid_context", checked_builder)
    cls = code_vector_storage.SessionLocal.class_
    event.listen(cls, "after_commit", committed)
    try:
        result = invoke(context, saved, handler=handler, threaded=threaded, top_k=2, budget=CodeContextBudget(max_chunks=1))
    finally:
        event.remove(cls, "after_commit", committed)
    assert_package(result.context)
    assert calls[0]["input"] == ["sample"] and len(calls) == 1
    assert result.query_sha256 == sha256(b"sample").hexdigest()
    assert result.source == "code_hybrid_query_context" and result.context.source == "bounded_hybrid_code_context"
    assert (result.prompt_tokens, result.total_tokens) == {"unknown": (None, None), "zero": (0, 0), "reported": (7, 9)}[usage]
    assert (result.context.source_metadata["prompt_tokens"], result.context.source_metadata["total_tokens"]) == (40, 50)
    assert result.context.recall_summary["omitted_by_top_k"] == 1
    assert len(result.context.omissions) == 1
    assert counts(hybrid_database) == before
    assert "EMBED_PRIVATE" not in repr(result) and "query_vector" not in repr(result)


@pytest.mark.parametrize("changes", [
    {"query": "a\nb"}, {"query": "!?!"}, {"query": " ".join(f"word{i}" for i in range(33))},
    {"top_k": True}, {"top_k": 21}, {"response_model": ""},
    {"budget": CodeContextBudget.model_construct(max_chunks=0, max_chars=100, max_bytes=100)},
])
def test_preflight_invalid_input_has_no_io(monkeypatch, changes):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input reached IO")
    monkeypatch.setattr(service, "_preflight_batch", forbidden)
    monkeypatch.setattr(service, "generate_query_embedding", forbidden)
    args: dict = {"query": "sample", "user_id": 1, "workspace_id": "w", "task_id": "t", "batch_id": "b", "config": config(), "response_model": "fixture-model-v1"}
    args.update(changes)
    with pytest.raises((ValueError, EmbeddingError)):
        asyncio.run(service.build_code_hybrid_query_context(**args))


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("kind", ["owner", "revision", "roundtrip"])
def test_revocation_never_builds_context(hybrid_database, context, target, monkeypatch, when, kind):
    saved = save(context)
    calls = []
    def revoke():
        with Session(hybrid_database) as session, session.begin():
            workspace = require_value(session.scalar(select(Workspace)))
            if kind == "owner":
                workspace.user_id = target["other_id"]
            elif kind == "revision":
                workspace.binding_revision += 1
            else:
                set_locked_workspace_root(workspace, "/other")
                set_locked_workspace_root(workspace, context.bound_root)
    def handler(request):
        calls.append(1)
        revoke()
        return query_response()
    def forbidden(*args, **kwargs):
        raise AssertionError("revoked query reached builder")
    monkeypatch.setattr(service, "build_code_hybrid_context", forbidden)
    if when == "before":
        revoke()
    with pytest.raises(WorkspaceNotAccessibleError):
        invoke(context, saved, handler=handler)
    assert len(calls) == (1 if when == "during" else 0)


@pytest.mark.parametrize("kind", ["http", "space", "zero", "budget", "timeout"])
def test_post_send_failure_is_not_retried_or_downgraded(hybrid_database, context, kind):
    saved = save(context)
    calls = []
    def handler(request):
        calls.append(1)
        if kind == "timeout":
            raise httpx.ReadTimeout("controlled")
        if kind == "http":
            return httpx.Response(503)
        return query_response(model="different" if kind == "space" else "fixture-model-v1", vector=(0, 0, 0) if kind == "zero" else (1, 0, 0))
    with pytest.raises((EmbeddingError, code_vector_search.CodeVectorSearchError, CodeContextError)):
        invoke(context, saved, handler=handler, budget=CodeContextBudget(max_bytes=1) if kind == "budget" else None)
    assert len(calls) == 1


def test_cancel_model_wait_closes_client_and_never_recalls(hybrid_database, context, closed_clients, monkeypatch):
    saved = save(context)
    def forbidden(*args, **kwargs):
        raise AssertionError("cancelled query reached recall")
    monkeypatch.setattr(service, "search_code_hybrid_batch", forbidden)
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
            return query_response()
        task = asyncio.create_task(service.build_code_hybrid_query_context(**arguments(context, saved, query="sample"), transport=httpx.MockTransport(handler)))
        await entered.wait()
        assert hybrid_database.pool.checkedout() == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert len(closed_clients) == 1 and closed_clients[0].is_closed


@pytest.mark.parametrize("stage", ["generated", "recalled"])
def test_mismatched_assembly_is_rejected(hybrid_database, context, monkeypatch, stage):
    saved = save(context)
    if stage == "generated":
        real = service.generate_query_embedding
        async def wrong(*args, **kwargs):
            return replace(await real(*args, **kwargs), query_sha256="f" * 64)
        monkeypatch.setattr(service, "generate_query_embedding", wrong)
    else:
        real_search = service.search_code_hybrid_batch
        def wrong_search(*args, **kwargs):
            return replace(real_search(*args, **kwargs), batch_id="f" * 32)
        monkeypatch.setattr(service, "search_code_hybrid_batch", wrong_search)
    with pytest.raises((CodeContextError, code_vector_search.CodeVectorSearchError)):
        invoke(context, saved)


@pytest.mark.parametrize("stage", ["preflight", "recall"])
def test_pending_cancel_at_sync_boundary_stops_next_stage(hybrid_database, context, monkeypatch, stage):
    saved = save(context)
    calls = []
    name = "_preflight_batch" if stage == "preflight" else "search_code_hybrid_batch"
    real = getattr(service, name)
    def cancel_after(*args, **kwargs):
        result = real(*args, **kwargs)
        require_value(asyncio.current_task()).cancel()
        return result
    def handler(request):
        calls.append(1)
        return query_response()
    def forbidden(*args, **kwargs):
        raise AssertionError("pending cancellation reached builder")
    monkeypatch.setattr(service, name, cancel_after)
    monkeypatch.setattr(service, "build_code_hybrid_context", forbidden)
    with pytest.raises(asyncio.CancelledError):
        invoke(context, saved, handler=handler)
    assert len(calls) == (0 if stage == "preflight" else 1)
