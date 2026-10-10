"""单批RRF的真实PG组合及通道错误边界。"""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace, CodeEmbeddingVector
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError, set_locked_workspace_root
from app.services.workspace.files import code_hybrid_search as service
from app.services.workspace.files import code_keyword_search as keyword
from app.services.workspace.files import code_vector_search as vector
from app.services.workspace.files import code_vector_storage as storage
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_vector_storage import context, counts, database, save, target
from tests.workspace.files.test_code_vector_storage_validation import batch, split_batch

__all__ = ["context", "database", "target"]


@pytest.fixture
def hybrid_database(database, monkeypatch):
    monkeypatch.setattr(keyword, "SessionLocal", storage.SessionLocal)
    monkeypatch.setattr(vector, "SessionLocal", storage.SessionLocal)
    return database


def recall(context, saved, query="alpha", **changes):
    args = {key: getattr(context, key) for key in ("user_id", "workspace_id", "task_id")}
    args.update(batch_id=saved.batch_id, config=config(), response_model="fixture-model-v1", query_vector=(1.0, 0.0, 0.0))
    args.update(changes)
    return service.search_code_hybrid_batch(query, **args)


def fixture_batch(vectors, texts):
    original = batch(texts=texts)
    return replace(original, embeddings=tuple(replace(entry, vector=values) for entry, values in zip(original.embeddings, vectors, strict=True)))


@pytest.mark.parametrize("top_k", [1, 2, 20])
def test_real_ranks_union_exact_ties_and_sources(hybrid_database, context, top_k):
    original = fixture_batch([(0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0)], ["alpha beta\n", "alpha\n", "beta\n"])
    saved = save(context, original)
    before = counts(hybrid_database)
    result = recall(context, saved, "alpha beta", top_k=top_k)
    # A的两路排名为1/2，B为2/1，分数精确相等后按来源顺序。
    assert [hit.chunk["chunk_id"] for hit in result.hits] == [entry.chunk.chunk_id for entry in original.embeddings][:top_k]
    assert result.hits[0].rrf_score == float(Fraction(1, 61) + Fraction(1, 62))
    assert result.hits[0].keyword_rank == 1 and result.hits[0].vector_rank == 2
    assert result.hits[0].matched_terms == ("alpha", "beta")
    assert result.hits[0].vector_distance == 1.0
    assert (result.keyword_candidate_count, result.vector_candidate_count, result.fused_candidate_count) == (3, 2, 3)
    assert result.excluded_zero_chunk_count == 1
    assert result.omitted_by_top_k == 3 - len(result.hits)
    assert [hit.rank for hit in result.hits] == list(range(1, len(result.hits) + 1))
    if len(result.hits) == 3:
        assert result.hits[2].vector_rank is None and result.hits[2].vector_distance is None
    assert counts(hybrid_database) == before
    assert result.metadata["content_trust"] == "untrusted_project_content"
    result.hits[0].chunk["text"] = "changed"
    assert recall(context, saved).hits[0].chunk["text"] != "changed"


@pytest.mark.parametrize("zeros,query,expected", [(False, "absent", 1), (True, "alpha", 1), (True, "absent", 0)])
def test_empty_channels_are_not_failures(hybrid_database, context, zeros, query, expected):
    saved = save(context, fixture_batch([(0.0, 0.0, 0.0) if zeros else (1.0, 0.0, 0.0)], ["alpha\n"]))
    result = recall(context, saved, query)
    assert len(result.hits) == result.fused_candidate_count == expected
    if expected:
        assert result.hits[0].rrf_score == float(Fraction(1, 61))


def test_partial_split_coverage_and_explicit_batch(hybrid_database, context):
    partial = split_batch()
    partial = replace(partial, embeddings=partial.embeddings[:1], truncated=True, incomplete_reasons=("chunk_budget",))
    saved = save(context, partial)
    save(context, batch(texts=["parent parent\n"]))
    result = recall(context, saved, "parent")
    assert result.batch_chunk_count == result.fused_candidate_count == 1
    assert result.metadata["truncated"] and result.metadata["incomplete_reasons"] == ["chunk_budget"]
    assert result.hits[0].chunk["part_count"] == 2


@pytest.mark.parametrize("kind", ["root", "revision", "owner", "roundtrip"])
def test_revocation_between_channels_cannot_return_partial_success(hybrid_database, context, target, monkeypatch, kind):
    saved = save(context)
    real = service.search_code_keyword_batch
    def revoke(*args, **kwargs):
        result = real(*args, **kwargs)
        with Session(hybrid_database) as session, session.begin():
            workspace = require_value(session.scalar(select(Workspace)))
            if kind == "owner":
                workspace.user_id = target["other_id"]
            elif kind == "revision":
                workspace.binding_revision += 1
            else:
                set_locked_workspace_root(workspace, "/other")
                if kind == "roundtrip":
                    set_locked_workspace_root(workspace, context.bound_root)
        return result
    monkeypatch.setattr(service, "search_code_keyword_batch", revoke)
    with pytest.raises(WorkspaceNotAccessibleError):
        recall(context, saved)


@pytest.mark.parametrize("channel", ["keyword", "embedding"])
def test_channel_failure_propagates(hybrid_database, context, monkeypatch, channel):
    saved = save(context)
    error = RuntimeError("controlled database failure")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(service, f"search_code_{channel}_batch", fail)
    with pytest.raises(RuntimeError) as caught:
        recall(context, saved)
    assert caught.value is error


@pytest.mark.parametrize("kind", ["metadata", "chunk"])
def test_mixed_snapshots_are_rejected(hybrid_database, context, monkeypatch, kind):
    saved = save(context, batch(texts=["alpha\n"]))
    real = service.search_code_embedding_batch
    def altered(**kwargs):
        result = deepcopy(real(**kwargs))
        if kind == "metadata":
            result.metadata["truncated"] = True
        else:
            result.hits[0].chunk["text"] = "changed"
        return result
    monkeypatch.setattr(service, "search_code_embedding_batch", altered)
    with pytest.raises(service.CodeHybridSearchError, match="code_hybrid_source_inconsistent"):
        recall(context, saved)


def test_bad_nonmatching_zero_vector_tail_is_not_hidden(hybrid_database, context):
    saved = save(context, fixture_batch([(1.0, 0.0, 0.0), (0.0, 0.0, 0.0)], ["alpha\n", "nothing\n"]))
    with Session(hybrid_database) as session, session.begin():
        row = require_value(session.scalar(select(CodeEmbeddingVector).order_by(CodeEmbeddingVector.ordinal.desc())))
        row.content = "tampered"
    with pytest.raises(keyword.CodeKeywordSearchError, match="code_keyword_batch_inconsistent"):
        recall(context, saved, top_k=1)


@pytest.mark.parametrize("top_k", [0, 21, True, 1.5])
def test_invalid_top_k_never_calls_channels(monkeypatch, top_k):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid top_k reached channel")
    monkeypatch.setattr(service, "search_code_keyword_batch", forbidden)
    with pytest.raises(service.CodeHybridSearchError, match="code_hybrid_query_invalid"):
        service.search_code_hybrid_batch("alpha", user_id=1, workspace_id="w", task_id="t", batch_id="b", config=config(), response_model="fixture-model-v1", query_vector=(1.0, 0.0, 0.0), top_k=top_k)


@pytest.mark.parametrize("changes,error", [({"query_vector": (0.0, 0.0, 0.0)}, vector.CodeVectorSearchError), ({"response_model": "different"}, WorkspaceNotAccessibleError)])
def test_invalid_vector_or_space_never_falls_back_to_keyword(hybrid_database, context, changes, error):
    saved = save(context, batch(texts=["alpha\n"]))
    with pytest.raises(error):
        recall(context, saved, **changes)
