"""真实PostgreSQL授权/距离与受控HTTPX查询串联，模型等待不持有事务。"""

import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
import json
from typing import Any

import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import (
    CodeEmbeddingBatch,
    CodeEmbeddingVector,
    Conversation,
    Task,
    Workspace,
)
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    set_locked_workspace_root,
)
from app.services.model.embedding_config import EmbeddingError
from app.services.model import query_embeddings
from app.services.workspace.files import code_query_search as service
from app.services.workspace.files import code_vector_search as recall_service
from app.services.workspace.files import code_vector_storage as storage
from tests.assertions import require_value
from tests.model.test_code_embeddings import TrackedStream, config, response
from tests.model.test_query_embeddings import closed_clients
from tests.workspace.files.test_code_vector_search import source_vectors
from tests.workspace.files.test_code_vector_storage import (
    context,
    counts,
    database,
    save,
    target,
)
from tests.workspace.files.test_code_vector_storage_validation import split_batch

# 复用根隔离库和领域资源追踪；不连接开发业务表。
__all__ = ["closed_clients", "context", "database", "target"]


@pytest.fixture
def query_database(database, monkeypatch):
    monkeypatch.setattr(service, "SessionLocal", storage.SessionLocal)
    monkeypatch.setattr(recall_service, "SessionLocal", storage.SessionLocal)
    return database


def query_response(*, vector=(1.0, 0.0, 0.0), model="fixture-model-v1", usage=True):
    value = response(model=model, usage=usage)
    value["data"][0]["embedding"] = list(vector)
    return httpx.Response(200, json=value)


def arguments(context, saved, **changes: Any):
    return {
        key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")
    } | {
        "query": "  查找任务取消\n",
        "batch_id": saved.batch_id,
        "config": config(),
        "response_model": "fixture-model-v1",
        **changes,
    }


def invoke(context, saved, *, handler=None, **changes: Any):
    handler = handler or (lambda request: query_response())
    return asyncio.run(
        service.search_code_query(
            **arguments(context, saved, **changes),
            transport=httpx.MockTransport(handler),
        )
    )


def forbidden(*args, **kwargs):
    raise AssertionError("denied query reached HTTP or vector ranking")


@pytest.mark.parametrize("usage", [True, False, "zero"])
def test_two_read_transactions_surround_one_http_request_and_keep_provenance(
    query_database, context, closed_clients, usage
):
    source = source_vectors([(0, 1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 0)])
    saved = save(context, source)
    before = counts(query_database)
    statements = []
    calls = []
    commits = []

    def collect(
        connection, cursor, statement, parameters, execution_context, executemany
    ):
        if "<=>" in statement:
            assert len(closed_clients) == 1 and closed_clients[0].is_closed
        statements.append(statement)

    def committed(session):
        commits.append("commit")

    async def handler(request):
        assert query_database.pool.checkedout() == 0
        assert commits == ["commit"]
        assert not any("<=>" in statement for statement in statements)
        assert not any(
            "code_embedding_vectors" in statement for statement in statements
        )
        await asyncio.sleep(0)
        assert query_database.pool.checkedout() == 0
        assert json.loads(request.content) == {
            "model": "fixture-model",
            "input": ["  查找任务取消\n"],
            "encoding_format": "float",
        }
        calls.append(request)
        value = response(usage=usage is True)
        value["data"][0]["embedding"] = [1, 0, 0]
        if usage == "zero":
            value["usage"] = {"prompt_tokens": 0, "total_tokens": 0}
        return httpx.Response(200, json=value)

    session_class = storage.SessionLocal.class_
    event.listen(query_database, "before_cursor_execute", collect)
    event.listen(session_class, "after_commit", committed)
    try:
        result = invoke(context, saved, handler=handler, top_k=1)
    finally:
        event.remove(query_database, "before_cursor_execute", collect)
        event.remove(session_class, "after_commit", committed)

    assert commits == ["commit", "commit"] and len(calls) == len(closed_clients) == 1
    assert result.query_sha256 == sha256("  查找任务取消\n".encode()).hexdigest()
    assert (
        result.requested_model,
        result.response_model,
        result.request_count,
        result.source,
    ) == ("fixture-model", "fixture-model-v1", 1, "query_embedding")
    expected_usage = (
        (2, 2) if usage is True else ((0, 0) if usage == "zero" else (None, None))
    )
    assert (result.prompt_tokens, result.total_tokens) == expected_usage
    assert result.recall.batch_id == saved.batch_id
    assert result.recall.space_id == saved.space_id
    assert result.recall.hits[0].chunk == json.loads(
        json.dumps(asdict(source.embeddings[1].chunk))
    )
    assert result.recall.hits[0].distance == 0.0
    assert (
        result.recall.searchable_chunk_count,
        result.recall.excluded_zero_chunk_count,
        result.recall.omitted_by_top_k,
    ) == (3, 1, 2)
    assert any("<=>" in statement for statement in statements)
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE"))
        for statement in statements
    )
    assert counts(query_database) == before
    projection = asdict(result)
    assert "vector" not in projection and "query" not in projection
    assert "EMBED_PRIVATE" not in repr(result) and "/preserved" not in repr(result)
    assert "provider.invalid" not in repr(result)


@pytest.mark.parametrize("dimensions", [1, 2, 4096])
def test_dimension_boundaries_complete_real_single_batch_recall(
    query_database, context, dimensions
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
    assert (
        result.recall.dimensions == dimensions and result.recall.hits[0].distance == 0.0
    )


def test_key_rotation_and_explicit_batch_keep_scope(query_database, context):
    saved = save(context, source_vectors([(-1, 0, 0)]))
    save(context, source_vectors([(1, 0, 0)]))
    active = config(api_key=SecretStr("ROTATED_PRIVATE"))
    result = invoke(context, saved, config=active)
    assert (
        result.recall.batch_id == saved.batch_id
        and result.recall.hits[0].distance == 2.0
    )
    assert "ROTATED_PRIVATE" not in repr(result)


def test_twenty_candidates_and_explicit_top_k_keep_the_complete_batch(
    query_database, context
):
    saved = save(context, source_vectors([(1, index, 0) for index in range(20)]))
    result = invoke(context, saved, top_k=20)
    assert result.recall.batch_chunk_count == result.recall.searchable_chunk_count == 20
    assert result.recall.omitted_by_top_k == 0 and len(result.recall.hits) == 20


def test_partial_definition_and_all_zero_batch_preserve_coverage(
    query_database, context
):
    original = split_batch()
    partial = replace(
        original,
        embeddings=original.embeddings[:1],
        truncated=True,
        incomplete_reasons=("chunk_budget",),
    )
    saved = save(context, partial)
    result = invoke(context, saved, top_k=1)
    assert result.recall.metadata["truncated"] is True
    assert result.recall.metadata["incomplete_reasons"] == ["chunk_budget"]
    assert result.recall.hits[0].chunk["part_count"] == 2
    saved_zero = save(context, source_vectors([(0, 0, 0)]))
    zero = invoke(context, saved_zero)
    assert zero.recall.hits == () and zero.recall.excluded_zero_chunk_count == 1


@pytest.mark.parametrize(
    "kind",
    [
        "other-user",
        "missing-workspace",
        "sibling-task",
        "unknown-batch",
        "owner",
        "conversation",
        "missing-conversation",
    ],
)
def test_initial_scope_failure_sends_nothing(
    query_database, context, target, monkeypatch, kind
):
    saved = save(context)
    changes = {}
    with Session(query_database) as session, session.begin():
        if kind == "other-user":
            changes["user_id"] = target["other_id"]
        elif kind == "missing-workspace":
            changes["workspace_id"] = "f" * 32
        elif kind == "sibling-task":
            changes["task_id"] = "d" * 32
        elif kind == "unknown-batch":
            changes["batch_id"] = "f" * 32
        elif kind == "owner":
            require_value(session.scalar(select(Workspace))).user_id = target[
                "other_id"
            ]
        elif kind == "conversation":
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
        else:
            session.delete(
                require_value(session.get(Conversation, target["conversation_pk"]))
            )
    monkeypatch.setattr(recall_service, "rank_batch_vectors", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        invoke(context, saved, handler=forbidden, **changes)


@pytest.mark.parametrize(
    "kind", ["provider", "model", "version", "dimensions", "dimension-option"]
)
def test_initial_wrong_space_sends_nothing(query_database, context, kind):
    saved = save(context)
    active = config(
        **{
            "provider": {"base_url": "https://another.invalid/v1"},
            "model": {"model": "other-model"},
            "version": {},
            "dimensions": {"dimensions": 4},
            "dimension-option": {"request_dimensions": True},
        }[kind]
    )
    with pytest.raises(WorkspaceNotAccessibleError):
        invoke(
            context,
            saved,
            handler=forbidden,
            config=active,
            response_model="fixture-model-v2"
            if kind == "version"
            else "fixture-model-v1",
        )


@pytest.mark.parametrize("kind", ["root", "revision", "unbind", "roundtrip"])
def test_initial_stale_binding_sends_nothing(query_database, context, kind):
    saved = save(context)
    with Session(query_database) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace).with_for_update()))
        if kind == "revision":
            workspace.binding_revision += 1
        elif kind == "roundtrip":
            set_locked_workspace_root(workspace, "/other")
            set_locked_workspace_root(workspace, context.bound_root)
        else:
            set_locked_workspace_root(workspace, None if kind == "unbind" else "/other")
    with pytest.raises(WorkspaceNotAccessibleError):
        invoke(context, saved, handler=forbidden)


@pytest.mark.parametrize(
    "change",
    [
        {"truncated": None},
        {"truncated": True},
        {"incomplete_reasons": None},
        {"embedding_space_id": "f" * 64},
        {"response_model": "wrong"},
        {"dimensions": 2},
        {"requested_model": "wrong"},
    ],
)
def test_inconsistent_initial_batch_sends_nothing(query_database, context, change):
    saved = save(context)
    with Session(query_database) as session, session.begin():
        row = require_value(session.scalar(select(CodeEmbeddingBatch)))
        row.source_metadata = {**row.source_metadata, **change}
    with pytest.raises(
        service.CodeVectorSearchError, match="code_embedding_batch_inconsistent"
    ):
        invoke(context, saved, handler=forbidden)


@pytest.mark.parametrize(
    "kind",
    [
        "owner",
        "conversation",
        "missing-conversation",
        "task-delete",
        "batch-delete",
        "root",
        "revision",
        "unbind",
        "roundtrip",
        "metadata",
    ],
)
def test_changes_during_http_wait_are_committed_then_reauthorized(
    query_database, context, target, monkeypatch, kind
):
    saved = save(context)
    calls = []

    async def handler(request):
        assert query_database.pool.checkedout() == 0
        await asyncio.sleep(0)
        # 独立连接真实提交，证明第一段归属锁没有跨模型等待保留。
        with Session(query_database) as session, session.begin():
            workspace = require_value(
                session.scalar(select(Workspace).with_for_update())
            )
            if kind == "owner":
                workspace.user_id = target["other_id"]
            elif kind == "conversation":
                require_value(
                    session.get(Conversation, target["conversation_pk"])
                ).user_id = target["other_id"]
            elif kind == "missing-conversation":
                session.delete(
                    require_value(session.get(Conversation, target["conversation_pk"]))
                )
            elif kind == "task-delete":
                session.delete(require_value(session.get(Task, target["task_pk"])))
            elif kind == "batch-delete":
                session.delete(
                    require_value(session.scalar(select(CodeEmbeddingBatch)))
                )
            elif kind == "revision":
                workspace.binding_revision += 1
            elif kind == "roundtrip":
                set_locked_workspace_root(workspace, "/other")
                set_locked_workspace_root(workspace, context.bound_root)
            elif kind == "metadata":
                row = require_value(session.scalar(select(CodeEmbeddingBatch)))
                row.source_metadata = {**row.source_metadata, "response_model": "wrong"}
            else:
                set_locked_workspace_root(
                    workspace, None if kind == "unbind" else "/other"
                )
        assert query_database.pool.checkedout() == 0
        calls.append(request)
        return query_response()

    monkeypatch.setattr(recall_service, "rank_batch_vectors", forbidden)
    error = (
        service.CodeVectorSearchError
        if kind == "metadata"
        else WorkspaceNotAccessibleError
    )
    with pytest.raises(error):
        invoke(context, saved, handler=handler)
    assert len(calls) == 1


def test_changed_reported_version_never_queries_distances(
    query_database, context, monkeypatch
):
    saved = save(context)
    monkeypatch.setattr(recall_service, "rank_batch_vectors", forbidden)
    with pytest.raises(
        service.CodeVectorSearchError, match="code_embedding_query_result_invalid"
    ):
        invoke(
            context,
            saved,
            handler=lambda request: query_response(model="fixture-model-v2"),
        )


@pytest.mark.parametrize(
    "vector,code",
    [
        ((0.0, 0.0, 0.0), "code_embedding_query_zero"),
        ((1e-50, 0.0, 0.0), "code_embedding_query_zero"),
        ((1e39, 0.0, 0.0), "code_embedding_query_invalid"),
    ],
)
def test_finite_generation_is_still_checked_for_cosine_use(
    query_database, context, monkeypatch, vector, code
):
    saved = save(context)
    monkeypatch.setattr(recall_service, "rank_batch_vectors", forbidden)
    with pytest.raises(service.CodeVectorSearchError, match=code):
        invoke(context, saved, handler=lambda request: query_response(vector=vector))


@pytest.mark.parametrize("kind", ["http", "transport", "timeout", "malformed"])
def test_generation_failure_never_reaches_second_transaction(
    query_database, context, monkeypatch, kind
):
    saved = save(context)
    calls = []

    def handler(request):
        assert query_database.pool.checkedout() == 0
        calls.append(request)
        if kind == "http":
            return httpx.Response(500, text="PRIVATE_SUPPLIER_BODY")
        if kind == "transport":
            raise httpx.ConnectError("PRIVATE_CONNECTION")
        if kind == "timeout":
            raise httpx.ReadTimeout("PRIVATE_TIMEOUT")
        return httpx.Response(200, json={"model": "fixture-model-v1", "data": []})

    monkeypatch.setattr(service, "search_code_embedding_batch", forbidden)
    with pytest.raises(EmbeddingError) as caught:
        invoke(context, saved, handler=handler)
    assert len(calls) == 1 and "PRIVATE" not in str(caught.value)


def test_real_invalid_tail_distance_does_not_publish_top_one(query_database, context):
    saved = save(context, source_vectors([(1, 0, 0), (0, 1e-40, 0)]))
    with pytest.raises(
        service.CodeVectorSearchError, match="code_embedding_distance_invalid"
    ):
        invoke(context, saved, top_k=1)


def test_candidate_deletion_during_wait_is_not_partial_success(query_database, context):
    saved = save(context, source_vectors([(1, 0, 0), (0, 1, 0)]))

    def handler(request):
        with Session(query_database) as session, session.begin():
            session.delete(
                require_value(
                    session.scalar(
                        select(CodeEmbeddingVector).order_by(
                            CodeEmbeddingVector.ordinal.desc()
                        )
                    )
                )
            )
        return query_response()

    with pytest.raises(
        service.CodeVectorSearchError, match="code_embedding_batch_inconsistent"
    ):
        invoke(context, saved, handler=handler, top_k=1)


@pytest.mark.parametrize("phase", [1, 2])
def test_transaction_exit_failure_cannot_send_or_publish_success(
    query_database, context, phase
):
    saved = save(context)
    commits = []
    calls = []

    def fail_commit(session):
        commits.append(1)
        if len(commits) == phase:
            raise RuntimeError("injected transaction exit failure")

    def handler(request):
        calls.append(request)
        return query_response()

    session_class = storage.SessionLocal.class_
    event.listen(session_class, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected transaction exit failure"):
            invoke(context, saved, handler=handler)
    finally:
        event.remove(session_class, "before_commit", fail_commit)
    assert len(calls) == phase - 1 and len(commits) == phase


def test_external_cancel_during_response_closes_http_without_recall(
    query_database, context, monkeypatch
):
    saved = save(context)
    stream = TrackedStream(b"{", delay=20)
    monkeypatch.setattr(service, "search_code_embedding_batch", forbidden)

    async def scenario():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, stream=stream
            )
        )
        task = asyncio.create_task(
            service.search_code_query(**arguments(context, saved), transport=transport)
        )
        await asyncio.wait_for(stream.started.wait(), timeout=2)
        assert query_database.pool.checkedout() == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert stream.closed


def test_wall_timeout_closes_response_without_recall(
    query_database, context, monkeypatch
):
    saved = save(context)
    stream = TrackedStream(b"{", delay=20)
    monkeypatch.setattr(service, "search_code_embedding_batch", forbidden)
    # 仅缩短受控测试的等待预算，生产配置仍要求1～60秒。
    active = config().model_copy(update={"timeout_seconds": 0.01})
    with pytest.raises(EmbeddingError) as caught:
        invoke(
            context,
            saved,
            config=active,
            handler=lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, stream=stream
            ),
        )
    assert caught.value.code == "embedding_timeout" and stream.closed


def test_client_exit_failure_cannot_reach_recall(
    query_database, context, monkeypatch, closed_clients
):
    saved = save(context)
    monkeypatch.setattr(service, "search_code_embedding_batch", forbidden)
    base_client = httpx.AsyncClient

    class FailedExitClient(base_client):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            raise RuntimeError("PRIVATE_CLIENT_EXIT")

    monkeypatch.setattr(query_embeddings.httpx, "AsyncClient", FailedExitClient)
    with pytest.raises(EmbeddingError) as caught:
        invoke(context, saved)
    assert caught.value.code == "embedding_request_failed" and "PRIVATE" not in str(
        caught.value
    )
    assert len(closed_clients) == 1 and closed_clients[0].is_closed
