"""真实PostgreSQL精确召回、来源保留、当前授权与绑定隔离。"""

from dataclasses import asdict, replace
import json

from pydantic import SecretStr
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import CodeEmbeddingBatch, CodeEmbeddingVector, Conversation, Workspace
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    set_locked_workspace_root,
)
from app.services.workspace.files import code_vector_search as service
from app.services.workspace.files import code_vector_storage as storage
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_vector_storage import (
    context,
    counts,
    database,
    save,
    target,
)
from tests.workspace.files.test_code_vector_storage_validation import batch, split_batch

# 显式复用领域夹具；它们仍依赖根conftest的随机数据库/私有schema。
__all__ = ["context", "database", "target"]


@pytest.fixture
def search_database(database, monkeypatch):
    # 存储与召回使用同一受跟踪Session工厂，父夹具核对事务与连接已释放。
    monkeypatch.setattr(service, "SessionLocal", storage.SessionLocal)
    return database


def source_vectors(vectors, *, active=None):
    original = batch(
        active=active,
        texts=[
            f"def sample_{index}(): return {index}\n" for index in range(len(vectors))
        ],
    )
    return replace(
        original,
        embeddings=tuple(
            replace(entry, vector=tuple(vector))
            for entry, vector in zip(original.embeddings, vectors, strict=True)
        ),
    )


def recall(context, saved, **changes):
    arguments = {
        key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")
    }
    arguments.update(
        batch_id=saved.batch_id,
        config=config(),
        response_model="fixture-model-v1",
        query_vector=(1.0, 0.0, 0.0),
    )
    arguments.update(changes)
    return service.search_code_embedding_batch(**arguments)


@pytest.mark.parametrize("top_k", [1, 2, 20])
def test_real_pgvector_orders_then_selects_top_k_without_writes(
    search_database, context, top_k
):
    original = source_vectors([(0, 1, 0), (-1, 0, 0), (1, 0, 0), (0, 0, 0)])
    saved = save(context, original)
    before = counts(search_database)
    statements = []

    def collect(
        connection, cursor, statement, parameters, execution_context, executemany
    ):
        statements.append(statement)

    event.listen(search_database, "before_cursor_execute", collect)
    try:
        result = recall(context, saved, top_k=top_k)
    finally:
        event.remove(search_database, "before_cursor_execute", collect)

    expected = [original.embeddings[index].chunk for index in (2, 0, 1)][:top_k]
    assert [hit.chunk for hit in result.hits] == json.loads(
        json.dumps([asdict(chunk) for chunk in expected])
    )
    assert [hit.distance for hit in result.hits] == [0.0, 1.0, 2.0][:top_k]
    assert [hit.rank for hit in result.hits] == list(range(1, len(expected) + 1))
    assert (result.batch_id, result.space_id, result.dimensions, result.top_k) == (
        saved.batch_id,
        saved.space_id,
        3,
        top_k,
    )
    assert result.batch_chunk_count == 4
    assert result.searchable_chunk_count == 3
    assert result.excluded_zero_chunk_count == 1
    assert result.omitted_by_top_k == 3 - len(expected)
    assert result.metadata["content_trust"] == "untrusted_project_content"
    assert result.metadata["prompt_tokens"] is None
    assert result.metadata["total_tokens"] is None
    assert result.metadata["files"] == json.loads(
        json.dumps([asdict(file) for file in original.files])
    )
    assert any("<=>" in statement and "LIMIT" in statement for statement in statements)
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE"))
        for statement in statements
    )
    assert counts(search_database) == before
    assert "EMBED_PRIVATE" not in repr(result) and "/preserved" not in repr(result)


def test_equal_distance_uses_original_ordinal_and_returns_detached_copies(
    search_database, context
):
    original = source_vectors([(0, -1, 0), (0, 1, 0), (0, 0, 1)])
    saved = save(context, original)
    result = recall(context, saved, top_k=20)
    assert [hit.chunk["chunk_id"] for hit in result.hits] == [
        entry.chunk.chunk_id for entry in original.embeddings
    ]
    assert [hit.distance for hit in result.hits] == [1.0, 1.0, 1.0]
    result.hits[0].chunk["symbol"]["name"] = "changed"
    result.metadata["files"][0]["relative_path"] = "changed.py"
    # 再查是为了验证快照嵌套对象不别名到数据库JSON，不是重复验收。
    fresh = recall(context, saved)
    assert fresh.hits[0].chunk["symbol"]["name"] != "changed"
    assert fresh.metadata["files"][0]["relative_path"] == "fixture.py"


@pytest.mark.parametrize("dimensions", [1, 2, 4096])
def test_variable_dimension_exact_query_works_without_approximate_index(
    search_database, context, dimensions
):
    active = config(dimensions=dimensions)
    vector = (1.0,) + (0.0,) * (dimensions - 1)
    saved = save(context, source_vectors([vector], active=active), active)
    result = recall(context, saved, config=active, query_vector=vector)
    assert result.dimensions == dimensions
    assert result.hits[0].distance == 0.0


def test_integer_query_and_float32_rounding_match_stored_direction(
    search_database, context
):
    saved = save(context, source_vectors([(0.1, -1.0, 0.0)]))
    result = recall(context, saved, query_vector=(0.1, -1, 0))
    assert result.hits[0].distance == pytest.approx(0.0, abs=1e-6)


def test_all_zero_batch_returns_explicit_empty_searchable_coverage(
    search_database, context
):
    saved = save(context, source_vectors([(0, 0, 0), (-0.0, 0, 0)]))
    result = recall(context, saved)
    assert result.hits == ()
    assert result.batch_chunk_count == result.excluded_zero_chunk_count == 2
    assert result.searchable_chunk_count == result.omitted_by_top_k == 0
    assert result.metadata["truncated"] is False


@pytest.mark.parametrize("partial", [False, True])
def test_definition_parts_and_original_truncation_survive_selection(
    search_database, context, partial
):
    original = split_batch()
    if partial:
        original = replace(
            original,
            embeddings=original.embeddings[:1],
            truncated=True,
            incomplete_reasons=("chunk_budget",),
        )
    saved = save(context, original)
    result = recall(context, saved, top_k=1)
    assert result.hits[0].chunk == json.loads(
        json.dumps(asdict(original.embeddings[0].chunk))
    )
    assert result.hits[0].chunk["part_count"] == 2
    assert result.metadata["truncated"] is partial
    assert result.metadata["incomplete_reasons"] == (
        ["chunk_budget"] if partial else []
    )
    assert result.omitted_by_top_k == (0 if partial else 1)


def test_explicit_batch_does_not_borrow_better_hit_from_another_batch(
    search_database, context
):
    first = save(context, source_vectors([(-1, 0, 0)]))
    save(context, source_vectors([(1, 0, 0)]))
    result = recall(context, first)
    assert result.batch_id == first.batch_id
    assert result.hits[0].distance == 2.0


def test_maximum_batch_and_top_k_keep_all_twenty_candidates(search_database, context):
    original = source_vectors([(1, index, 0) for index in range(20)])
    saved = save(context, original)
    result = recall(context, saved, top_k=20)
    assert result.batch_chunk_count == result.searchable_chunk_count == 20
    assert result.excluded_zero_chunk_count == result.omitted_by_top_k == 0
    assert [hit.rank for hit in result.hits] == list(range(1, 21))
    assert [hit.chunk["chunk_id"] for hit in result.hits] == [
        entry.chunk.chunk_id for entry in original.embeddings
    ]


@pytest.mark.parametrize(
    "kind",
    [
        "other-user",
        "owner-revoked",
        "other-project",
        "sibling-task",
        "foreign-conversation",
        "missing-conversation",
        "missing-task",
        "unknown-batch",
    ],
)
def test_scope_failure_happens_before_vector_distance_query(
    search_database, context, target, monkeypatch, kind
):
    saved = save(context)
    changes = {}
    with Session(search_database) as session, session.begin():
        if kind == "other-user":
            changes["user_id"] = target["other_id"]
        elif kind == "owner-revoked":
            require_value(session.scalar(select(Workspace))).user_id = target[
                "other_id"
            ]
        elif kind == "other-project":
            session.add(
                Workspace(
                    external_id="f" * 32,
                    name="其他项目",
                    user_id=context.user_id,
                    root_path="/other",
                )
            )
            changes["workspace_id"] = "f" * 32
        elif kind == "sibling-task":
            changes["task_id"] = "d" * 32
        elif kind == "foreign-conversation":
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
        elif kind == "missing-conversation":
            session.delete(
                require_value(session.get(Conversation, target["conversation_pk"]))
            )
        elif kind == "missing-task":
            changes["task_id"] = "f" * 32
        else:
            changes["batch_id"] = "f" * 32

    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized scope reached vector distance query")

    monkeypatch.setattr(service, "rank_batch_vectors", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        recall(context, saved, **changes)


@pytest.mark.parametrize("kind", ["root", "revision", "unbind", "roundtrip"])
def test_current_binding_rejects_old_batch_before_distance_query(
    search_database, context, monkeypatch, kind
):
    saved = save(context)
    with Session(search_database) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace).with_for_update()))
        if kind == "revision":
            workspace.binding_revision += 1
        elif kind == "roundtrip":
            set_locked_workspace_root(workspace, "/other")
            set_locked_workspace_root(workspace, context.bound_root)
        else:
            set_locked_workspace_root(workspace, None if kind == "unbind" else "/other")

    def forbidden(*args, **kwargs):
        raise AssertionError("old binding reached vector distance query")

    monkeypatch.setattr(service, "rank_batch_vectors", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        recall(context, saved)


@pytest.mark.parametrize(
    "kind", ["provider", "model", "version", "dimensions", "dimension-option"]
)
def test_exact_model_space_is_required_before_distance_query(
    search_database, context, monkeypatch, kind
):
    saved = save(context)
    active = config(
        **{
            "provider": {"base_url": "https://another.invalid/v1"},
            "model": {"model": "another-model"},
            "version": {},
            "dimensions": {"dimensions": 4},
            "dimension-option": {"request_dimensions": True},
        }[kind]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("wrong model space reached vector distance query")

    monkeypatch.setattr(service, "rank_batch_vectors", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        recall(
            context,
            saved,
            config=active,
            response_model="fixture-model-v2"
            if kind == "version"
            else "fixture-model-v1",
            query_vector=(1.0,) + (0.0,) * (active.dimensions - 1),
        )


def test_key_rotation_does_not_change_model_space(search_database, context):
    saved = save(context)
    rotated = config(api_key=SecretStr("ROTATED_PRIVATE"))
    result = recall(context, saved, config=rotated)
    assert result.space_id == saved.space_id and len(result.hits) == 1
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize(
    "change",
    [
        {"truncated": None},
        {"truncated": True},
        {"incomplete_reasons": None},
        {"embedding_space_id": "f" * 64},
        {"response_model": "different-version"},
    ],
)
def test_corrupt_batch_metadata_is_not_fabricated_complete(
    search_database, context, change
):
    saved = save(context)
    with Session(search_database) as session, session.begin():
        row = require_value(session.scalar(select(CodeEmbeddingBatch)))
        row.source_metadata = {**row.source_metadata, **change}
    with pytest.raises(
        service.CodeVectorSearchError, match="code_embedding_batch_inconsistent"
    ):
        recall(context, saved)


@pytest.mark.parametrize("kind", ["missing-vector", "wrong-ordinal", "wrong-chunk-id"])
def test_inconsistent_candidate_rows_fail_without_partial_hits(
    search_database, context, kind
):
    saved = save(context, source_vectors([(1, 0, 0), (0, 1, 0)]))
    with Session(search_database) as session, session.begin():
        row = require_value(
            session.scalar(
                select(CodeEmbeddingVector).order_by(CodeEmbeddingVector.ordinal.desc())
            )
        )
        if kind == "missing-vector":
            session.delete(row)
        elif kind == "wrong-ordinal":
            row.ordinal = 19
        else:
            row.chunk_metadata = {**row.chunk_metadata, "chunk_id": "f" * 64}
    with pytest.raises(
        service.CodeVectorSearchError, match="code_embedding_batch_inconsistent"
    ):
        recall(context, saved, top_k=1)


def test_real_pgvector_nonfinite_tail_distance_is_not_hidden_by_top_k(
    search_database, context
):
    # float32可以保存该非零值，但余弦内部平方可能下溢，正交距离产生NaN。
    saved = save(context, source_vectors([(1, 0, 0), (0, 1e-40, 0)]))
    with pytest.raises(
        service.CodeVectorSearchError, match="code_embedding_distance_invalid"
    ):
        recall(context, saved, top_k=1)


def test_transaction_exit_failure_does_not_return_success(search_database, context):
    saved = save(context)

    def fail_commit(session):
        raise RuntimeError("injected read transaction exit failure")

    session_class = service.SessionLocal.class_
    event.listen(session_class, "before_commit", fail_commit)
    try:
        with pytest.raises(
            RuntimeError, match="injected read transaction exit failure"
        ):
            recall(context, saved)
    finally:
        event.remove(session_class, "before_commit", fail_commit)
