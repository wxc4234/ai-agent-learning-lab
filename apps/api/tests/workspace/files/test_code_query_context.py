"""真实PG查询/召回/上下文组合；HTTP受控，证明资源关闭和当前授权边界。"""

import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
import json

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import CodeEmbeddingBatch, CodeEmbeddingVector, Conversation, Workspace
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    set_locked_workspace_root,
)
from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.files import code_query_context as service
from app.services.workspace.files import (
    code_query_search,
    code_vector_search,
    code_vector_storage,
)
from app.services.workspace.files.code_context import (
    CodeContextBudget,
    CodeContextError,
)
from tests.assertions import require_value
from tests.model.test_code_embeddings import TrackedStream, config
from tests.model.test_query_embeddings import closed_clients
from tests.workspace.files.test_code_context import assert_package
from tests.workspace.files.test_code_query_search import arguments, query_response
from tests.workspace.files.test_code_vector_search import source_vectors
from tests.workspace.files.test_code_vector_storage import (
    context,
    counts,
    database,
    save,
    target,
)
from tests.workspace.files.test_code_vector_storage_validation import split_batch

__all__ = ["closed_clients", "context", "database", "target"]


@pytest.fixture
def context_database(database, monkeypatch):
    # 两段授权和召回复用父夹具跟踪的Session；结束时核对事务与连接释放。
    monkeypatch.setattr(
        code_query_search, "SessionLocal", code_vector_storage.SessionLocal
    )
    monkeypatch.setattr(
        code_vector_search, "SessionLocal", code_vector_storage.SessionLocal
    )
    return database


def invoke(context, saved, *, handler=None, **changes):
    return asyncio.run(
        service.build_code_query_context(
            **arguments(context, saved, **changes),
            transport=httpx.MockTransport(
                handler or (lambda request: query_response())
            ),
        )
    )


def forbidden(*args, **kwargs):
    raise AssertionError("rejected operation reached model or builder")


@pytest.mark.parametrize("usage", ["unknown", "zero", "reported"])
def test_real_two_transactions_and_http_close_before_builder_without_writes(
    context_database, context, monkeypatch, closed_clients, usage
):
    source = replace(
        source_vectors([(0, 1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 0)]),
        prompt_tokens=40,
        total_tokens=50,
        request_count=2,
    )
    saved = save(context, source)
    before = counts(context_database)
    commits, calls, statements = [], [], []
    original_builder = service.build_code_context

    def committed(session):
        commits.append("commit")

    def collect(
        connection, cursor, statement, parameters, execution_context, executemany
    ):
        statements.append(statement)

    async def handler(request):
        assert context_database.pool.checkedout() == 0 and commits == ["commit"]
        await asyncio.sleep(0)
        assert context_database.pool.checkedout() == 0
        calls.append(request)
        value = json.loads(query_response(usage=False).content)
        if usage != "unknown":
            value["usage"] = {
                "prompt_tokens": 0 if usage == "zero" else 7,
                "total_tokens": 0 if usage == "zero" else 9,
            }
        return httpx.Response(200, json=value)

    def build(snapshot, *, budget):
        assert context_database.pool.checkedout() == 0 and commits == [
            "commit",
            "commit",
        ]
        assert len(closed_clients) == 1 and closed_clients[0].is_closed
        assert len(calls) == 1
        return original_builder(snapshot, budget=budget)

    session_class = code_vector_storage.SessionLocal.class_
    event.listen(session_class, "after_commit", committed)
    event.listen(context_database, "before_cursor_execute", collect)
    monkeypatch.setattr(service, "build_code_context", build)
    try:
        result = invoke(
            context,
            saved,
            handler=handler,
            top_k=2,
            budget=CodeContextBudget(max_chunks=1),
        )
    finally:
        event.remove(session_class, "after_commit", committed)
        event.remove(context_database, "before_cursor_execute", collect)

    payload = assert_package(result.context)
    assert result.query_sha256 == sha256("  查找任务取消\n".encode()).hexdigest()
    assert (result.requested_model, result.response_model, result.request_count) == (
        "fixture-model",
        "fixture-model-v1",
        1,
    )
    expected_usage = {"unknown": (None, None), "zero": (0, 0), "reported": (7, 9)}[
        usage
    ]
    assert (result.prompt_tokens, result.total_tokens) == expected_usage
    assert (
        result.context.source_metadata["prompt_tokens"],
        result.context.source_metadata["total_tokens"],
        result.context.source_metadata["request_count"],
    ) == (40, 50, 2)
    assert result.context.selected_chunks[0]["chunk"] == json.loads(
        json.dumps(asdict(source.embeddings[1].chunk))
    )
    assert result.context.selected_chunks[0]["distance"] == 0.0
    assert result.context.omissions[0].reasons == ("chunk_budget",)
    assert (
        payload["recall_omitted_by_top_k"] == 1 and payload["builder_omitted_hits"] == 1
    )
    assert result.context.recall_summary["excluded_zero_chunk_count"] == 1
    assert any("<=>" in statement for statement in statements)
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE"))
        for statement in statements
    )
    assert counts(context_database) == before
    assert "recall" not in asdict(result) and "query" not in asdict(result)
    assert "EMBED_PRIVATE" not in repr(result) and "/preserved" not in repr(result)


@pytest.mark.parametrize("dimensions", [1, 2, 4096])
def test_dimension_boundaries_flow_through_real_recall_to_bounded_context(
    context_database, context, dimensions
):
    active = config(dimensions=dimensions)
    vector = (1.0,) + (0.0,) * (dimensions - 1)
    saved = save(context, source_vectors([vector], active=active), active)
    result = invoke(
        context,
        saved,
        config=active,
        handler=lambda request: query_response(vector=vector),
    )
    assert_package(result.context)
    assert (
        result.context.dimensions == dimensions
        and result.context.selected_chunks[0]["distance"] == 0.0
    )


def test_partial_definition_original_coverage_and_empty_candidates_survive_composition(
    context_database, context
):
    original = split_batch()
    partial = replace(
        original,
        embeddings=original.embeddings[:1],
        truncated=True,
        incomplete_reasons=("chunk_budget",),
    )
    saved = save(context, partial)
    result = invoke(context, saved)
    assert_package(result.context)
    assert result.context.source_metadata["truncated"] is True
    assert result.context.selected_chunks[0]["chunk"]["part_count"] == 2
    zero = save(context, source_vectors([(0, 0, 0)]))
    empty = invoke(context, zero)
    assert_package(empty.context)
    assert (
        empty.context.selected_hit_count == 0
        and empty.context.recall_summary["excluded_zero_chunk_count"] == 1
    )


@pytest.mark.parametrize(
    "kind", ["other-owner", "sibling-task", "unknown-batch", "space", "binding"]
)
def test_initial_authorization_failure_sends_nothing_and_never_builds(
    context_database, context, target, monkeypatch, kind
):
    saved = save(context)
    changes = {}
    if kind == "other-owner":
        changes["user_id"] = target["other_id"]
    elif kind == "sibling-task":
        changes["task_id"] = "d" * 32
    elif kind == "unknown-batch":
        changes["batch_id"] = "f" * 32
    elif kind == "space":
        changes["config"] = config(base_url="https://another.invalid/v1")
    else:
        with Session(context_database) as session, session.begin():
            set_locked_workspace_root(
                require_value(session.scalar(select(Workspace))), "/other"
            )
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        invoke(context, saved, handler=forbidden, **changes)


@pytest.mark.parametrize("kind", ["owner", "conversation", "roundtrip", "batch-delete"])
def test_waiting_revoke_is_committed_and_prevents_any_context(
    context_database, context, target, monkeypatch, kind
):
    saved = save(context)
    calls = []

    async def handler(request):
        assert context_database.pool.checkedout() == 0
        await asyncio.sleep(0)
        with Session(context_database) as session, session.begin():
            workspace = require_value(
                session.scalar(select(Workspace).with_for_update())
            )
            if kind == "owner":
                workspace.user_id = target["other_id"]
            elif kind == "conversation":
                require_value(
                    session.get(Conversation, target["conversation_pk"])
                ).user_id = target["other_id"]
            elif kind == "roundtrip":
                set_locked_workspace_root(workspace, "/other")
                set_locked_workspace_root(workspace, context.bound_root)
            else:
                session.delete(
                    require_value(session.scalar(select(CodeEmbeddingBatch)))
                )
        calls.append(request)
        return query_response()

    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        invoke(context, saved, handler=handler)
    assert len(calls) == 1


def test_version_change_prevents_context_building(
    context_database, context, monkeypatch
):
    saved = save(context)
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(
        code_vector_search.CodeVectorSearchError,
        match="code_embedding_query_result_invalid",
    ):
        invoke(
            context,
            saved,
            handler=lambda request: query_response(model="fixture-model-v2"),
        )


def test_valid_but_too_small_budget_fails_after_one_model_request(
    context_database, context
):
    saved = save(context)
    calls = []

    def handler(request):
        calls.append(request)
        return query_response()

    with pytest.raises(CodeContextError, match="code_context_budget_too_small"):
        invoke(context, saved, handler=handler, budget=CodeContextBudget(max_bytes=1))
    assert len(calls) == 1 and context_database.pool.checkedout() == 0


def test_bad_source_provenance_fails_in_builder_after_successful_model_and_recall(
    context_database, context
):
    saved = save(context)
    calls = []
    with Session(context_database) as session, session.begin():
        vector = require_value(session.scalar(select(CodeEmbeddingVector)))
        vector.chunk_metadata = {**vector.chunk_metadata, "text_sha256": "f" * 64}

    def handler(request):
        calls.append(request)
        return query_response()

    with pytest.raises(CodeContextError, match="code_context_snapshot_invalid"):
        invoke(context, saved, handler=handler)
    assert len(calls) == 1 and context_database.pool.checkedout() == 0


@pytest.mark.parametrize("phase", [1, 2])
def test_transaction_exit_failure_never_constructs_context(
    context_database, context, monkeypatch, phase
):
    saved = save(context)
    commits, calls = [], []

    def fail_commit(session):
        commits.append(1)
        if len(commits) == phase:
            raise RuntimeError("injected transaction exit failure")

    def handler(request):
        calls.append(request)
        return query_response()

    monkeypatch.setattr(service, "build_code_context", forbidden)
    session_class = code_vector_storage.SessionLocal.class_
    event.listen(session_class, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected transaction exit failure"):
            invoke(context, saved, handler=handler)
    finally:
        event.remove(session_class, "before_commit", fail_commit)
    assert len(calls) == phase - 1


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_http_timeout_or_cancel_closes_resources_and_never_builds(
    context_database, context, monkeypatch, mode
):
    saved = save(context)
    stream = TrackedStream(b"{", delay=20)
    monkeypatch.setattr(service, "build_code_context", forbidden)

    async def scenario():
        active = (
            config()
            if mode == "cancel"
            else config().model_copy(update={"timeout_seconds": 0.01})
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, stream=stream
            )
        )
        task = asyncio.create_task(
            service.build_code_query_context(
                **arguments(context, saved, config=active), transport=transport
            )
        )
        if mode == "cancel":
            await asyncio.wait_for(stream.started.wait(), timeout=2)
            assert context_database.pool.checkedout() == 0
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(EmbeddingError) as caught:
                await task
            assert caught.value.code == "embedding_timeout"

    asyncio.run(scenario())
    assert stream.closed
