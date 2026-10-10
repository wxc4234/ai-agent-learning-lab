"""授权关键词召回的排序、完整性与真实PostgreSQL读取边界。"""

from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import CodeEmbeddingBatch, CodeEmbeddingVector, Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError, set_locked_workspace_root
from app.services.workspace.files import code_keyword_search as service
from app.services.workspace.files import code_vector_storage as storage
from tests.assertions import require_value
from tests.workspace.files.test_code_vector_storage import context, counts, database, save, target
from tests.workspace.files.test_code_vector_storage_validation import batch, split_batch

__all__ = ["context", "database", "target"]


@pytest.fixture
def search_database(database, monkeypatch):
    # 共用跟踪Session工厂，父夹具检查每次成功/失败后的连接与事务释放。
    monkeypatch.setattr(service, "SessionLocal", storage.SessionLocal)
    return database


def recall(context, saved, query="alpha beta", **changes):
    arguments = {key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")}
    arguments.update(batch_id=saved.batch_id, space_id=saved.space_id, top_k=5)
    arguments.update(changes)
    return service.search_code_keyword_batch(query, **arguments)


@pytest.mark.parametrize("changes", [
    {"query": ""}, {"query": "   "}, {"query": "!_!"}, {"query": "a\nb"},
    {"query": "a\x7fb"}, {"query": "a" * 2001}, {"query": "中" * 1500},
    {"query": "\ud800"}, {"query": " ".join(f"word{i}" for i in range(33))},
    {"query": None}, {"top_k": True}, {"top_k": 0}, {"top_k": 21}, {"space_id": "bad"},
])
def test_invalid_query_never_opens_transaction(monkeypatch, changes):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input reached database")
    monkeypatch.setattr(service, "SessionLocal", forbidden)
    arguments: dict = {"query": "alpha", "user_id": 1, "workspace_id": "w", "task_id": "t", "batch_id": "b", "space_id": "a" * 64}
    arguments.update(changes)
    with pytest.raises(service.CodeKeywordSearchError, match="^code_keyword_query_invalid$"):
        service.search_code_keyword_batch(**arguments)


@pytest.mark.parametrize("top_k", [1, 2, 20])
def test_ranking_counters_sources_and_read_only_sql(search_database, context, top_k):
    original = batch(texts=["alpha alpha\n", "ALPHA beta\n", "beta alpha\n", "unrelated\n"])
    # 词面通道不需要非零向量，也不应加载向量列。
    original = replace(original, embeddings=tuple(replace(entry, vector=(0.0, 0.0, 0.0)) for entry in original.embeddings))
    saved = save(context, original)
    save(context, batch(texts=["alpha beta extra\n"]))
    before = counts(search_database)
    statements = []
    def collect(connection, cursor, statement, parameters, execution_context, executemany):
        statements.append(statement)
    event.listen(search_database, "before_cursor_execute", collect)
    try:
        result = recall(context, saved, "ALPHA alpha beta", top_k=top_k)
    finally:
        event.remove(search_database, "before_cursor_execute", collect)
    selected = [original.embeddings[i].chunk for i in (1, 2, 0)][:top_k]
    assert [hit.chunk for hit in result.hits] == json.loads(json.dumps([asdict(chunk) for chunk in selected]))
    assert [hit.score for hit in result.hits] == [2, 2, 1][:top_k]
    assert result.hits[0].matched_terms == ("alpha", "beta")
    assert [hit.rank for hit in result.hits] == list(range(1, len(selected) + 1))
    assert (result.batch_chunk_count, result.matched_chunk_count, result.omitted_by_top_k) == (4, 3, 3 - len(selected))
    assert result.query_terms == ("alpha", "beta")
    assert result.metadata["files"] == json.loads(json.dumps([asdict(file) for file in original.files]))
    assert result.metadata["content_trust"] == "untrusted_project_content"
    assert not any("code_embedding_vectors.embedding" in statement for statement in statements)
    assert any("octet_length" in statement and "LIMIT" in statement for statement in statements)
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
    assert counts(search_database) == before
    assert "EMBED_PRIVATE" not in repr(result)


def test_symbols_paths_empty_results_and_detached_copies(search_database, context):
    saved = save(context, batch(texts=["nothing\n"]))
    result = recall(context, saved, "SAMPLE_0 fixture")
    assert result.hits[0].matched_terms == ("0", "fixture", "sample")
    result.hits[0].chunk["symbol"]["name"] = "changed"
    result.metadata["files"][0]["relative_path"] = "changed.py"
    fresh = recall(context, saved, "sample")
    assert fresh.hits[0].chunk["symbol"]["name"] == "sample_0"
    assert fresh.metadata["files"][0]["relative_path"] == "fixture.py"
    empty = recall(context, saved, "absent")
    assert empty.hits == () and empty.matched_chunk_count == empty.omitted_by_top_k == 0


def test_twenty_chunks_and_partial_coverage(search_database, context):
    saved = save(context, batch(texts=[f"alpha {i}\n" for i in range(20)]))
    result = recall(context, saved, "alpha", top_k=20)
    assert len(result.hits) == result.batch_chunk_count == 20
    partial = split_batch()
    partial = replace(partial, embeddings=partial.embeddings[:1], truncated=True, incomplete_reasons=("chunk_budget",))
    saved = save(context, partial)
    result = recall(context, saved, "parent", top_k=1)
    assert result.metadata["truncated"] is True
    assert result.metadata["incomplete_reasons"] == ["chunk_budget"]
    assert result.hits[0].chunk["part_count"] == 2
    assert result.omitted_by_top_k == 0


@pytest.mark.parametrize("kind", ["text", "ordinal", "missing", "large-text", "large-metadata", "chunk-id", "file", "scope", "coverage"])
def test_corrupt_nonmatching_tail_is_rejected_before_top_k(search_database, context, kind):
    saved = save(context, batch(texts=["alpha\n", "unrelated\n"]))
    if kind == "large-text":
        # 正常结构下超长正文在入库阶段就被拒绝，不伪造不存在的坏行。
        with pytest.raises(IntegrityError, match="ck_code_embedding_vectors_content"), Session(search_database) as session, session.begin():
            row = require_value(session.scalar(select(CodeEmbeddingVector)))
            row.content = "x" * 4097
        return
    with Session(search_database) as session, session.begin():
        row = require_value(session.scalar(select(CodeEmbeddingVector).order_by(CodeEmbeddingVector.ordinal.desc())))
        if kind == "missing":
            session.delete(row)
        elif kind == "text":
            row.content = "changed"
        elif kind == "ordinal":
            row.ordinal = 5
        elif kind in ("large-metadata", "chunk-id"):
            row.chunk_metadata = {**row.chunk_metadata, **({"padding": "x" * 17000} if kind == "large-metadata" else {"chunk_id": "bad"})}
        else:
            stored = require_value(session.scalar(select(CodeEmbeddingBatch)))
            metadata = deepcopy(stored.source_metadata)
            if kind == "file":
                metadata["files"][0]["sha256"] = "f" * 64
            elif kind == "scope":
                metadata["task_id"] = "f" * 32
            else:
                metadata["truncated"] = True
            stored.source_metadata = metadata
    with pytest.raises(service.CodeKeywordSearchError, match="^code_keyword_batch_inconsistent$"):
        recall(context, saved, "alpha", top_k=1)


@pytest.mark.parametrize("kind", ["user", "owner", "task", "workspace", "conversation", "batch", "space", "root", "revision", "unbind", "roundtrip"])
def test_current_authorization_precedes_body_read(search_database, context, target, monkeypatch, kind):
    saved = save(context)
    # 一次成功快照不能授权后续访问。
    recall(context, saved, "sample")
    changes = {}
    with Session(search_database) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace)))
        if kind == "user":
            changes["user_id"] = target["other_id"]
        elif kind == "owner":
            workspace.user_id = target["other_id"]
        elif kind in ("task", "workspace", "batch", "space"):
            changes[f"{kind}_id"] = "f" * (64 if kind == "space" else 32)
        elif kind == "conversation":
            require_value(session.get(Conversation, target["conversation_pk"])).user_id = target["other_id"]
        elif kind == "revision":
            workspace.binding_revision += 1
        else:
            set_locked_workspace_root(workspace, None if kind == "unbind" else "/other")
            if kind == "roundtrip":
                set_locked_workspace_root(workspace, context.bound_root)
    def forbidden(*args, **kwargs):
        raise AssertionError("unauthorized body read")
    monkeypatch.setattr(service, "read_batch_keyword_chunks", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        recall(context, saved, **changes)
