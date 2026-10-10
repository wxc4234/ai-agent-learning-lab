"""混合快照契约、实际JSON预算及无I/O构建。"""

from copy import deepcopy
from dataclasses import asdict, replace
from fractions import Fraction
import json
from typing import Any, cast

import pytest

from app.services.workspace.files import code_hybrid_context as service
from app.services.workspace.files.code_context import CodeContextBudget, CodeContextError
from app.services.workspace.files.code_hybrid_search import CodeHybridHit, CodeHybridSearchResult
from tests.workspace.files.test_code_context import recall, assert_package
from tests.workspace.files.test_code_vector_storage_validation import batch, split_batch


def hybrid(value=None):
    original = recall(value)
    return CodeHybridSearchResult(original.batch_id, original.space_id, original.top_k,
        original.batch_chunk_count, len(original.hits), len(original.hits), 0, len(original.hits), 0,
        original.metadata, tuple(CodeHybridHit(hit.rank, float(Fraction(2, 60 + hit.rank)),
            hit.rank, hit.rank, ("sample",), 1, hit.distance, hit.chunk) for hit in original.hits))


def test_preserves_named_evidence_and_detached_source():
    original = hybrid()
    before = deepcopy(asdict(original))
    result = service.build_code_hybrid_context(original)
    payload = assert_package(result)
    assert result.source == "bounded_hybrid_code_context"
    assert payload["retrieval_strategy"] == "single-batch-rrf-v1"
    assert result.source_metadata == original.metadata
    assert "distance" not in payload["chunks"][0]
    assert payload["chunks"][0]["vector_distance"] == 0.0
    assert payload["chunks"][0]["rrf_score"] == float(Fraction(2, 61))
    assert payload["chunks"][0]["matched_terms"] == ["sample"]
    assert asdict(original) == before
    result.selected_chunks[0]["chunk"]["text"] = "changed"
    assert asdict(original) == before


@pytest.mark.parametrize("field,reason", [("max_chars", "character_budget"), ("max_bytes", "byte_budget")])
def test_exact_json_boundary_and_one_less(field, reason):
    original = hybrid(batch(texts=['def sample_0(): return "中文\\t"\n']))
    full = service.build_code_hybrid_context(original)
    limit = full.context_chars if field == "max_chars" else full.context_bytes
    exact = service.build_code_hybrid_context(original, budget=CodeContextBudget(**{field: limit}))
    assert exact.context_text == full.context_text
    reduced = service.build_code_hybrid_context(original, budget=CodeContextBudget(**{field: limit - 1}))
    assert_package(reduced)
    assert reduced.selected_hit_count == 0 and reduced.omissions[0].reasons == (reason,)
    assert full.context_bytes > full.context_chars


def test_large_first_chunk_can_be_skipped_for_small_later_chunk():
    original = hybrid(batch(texts=["# " + "中" * 500 + "\n", "pass\n"]))
    full = service.build_code_hybrid_context(original)
    result = service.build_code_hybrid_context(original, budget=CodeContextBudget(max_bytes=full.context_bytes - 1800))
    assert_package(result)
    assert result.selected_hit_count == 1
    assert result.selected_chunks[0]["rank"] == 2
    assert result.omissions[0].source_rank == 1


def test_three_omission_levels_and_partial_definition():
    value = split_batch()
    value = replace(value, truncated=True, incomplete_reasons=("chunk_budget",))
    original = hybrid(value)
    # split_batch的实际符号为parent，需要真实可匹配词项。
    original = replace(original, hits=tuple(replace(hit, matched_terms=("parent",)) for hit in original.hits),
        batch_chunk_count=3, keyword_candidate_count=3, vector_candidate_count=3,
        fused_candidate_count=3, omitted_by_top_k=1)
    result = service.build_code_hybrid_context(original, budget=CodeContextBudget(max_chunks=1))
    payload = assert_package(result)
    assert payload["snapshot_truncated"] is True
    assert payload["recall_omitted_by_top_k"] == 1 and payload["builder_omitted_hits"] == 1
    assert result.selected_chunks[0]["chunk"]["part_count"] == 2
    assert result.omissions[0].reasons == ("chunk_budget",)


@pytest.mark.parametrize("channel", ["keyword", "vector", "empty"])
def test_single_channel_and_empty(channel):
    original = hybrid(batch(texts=["pass\n"]))
    hit = original.hits[0]
    if channel == "keyword":
        original = replace(original, vector_candidate_count=0, excluded_zero_chunk_count=1,
            hits=(replace(hit, vector_rank=None, vector_distance=None, rrf_score=float(Fraction(1, 61))),))
    elif channel == "vector":
        original = replace(original, keyword_candidate_count=0,
            hits=(replace(hit, keyword_rank=None, keyword_score=None, matched_terms=(), rrf_score=float(Fraction(1, 61))),))
    else:
        original = replace(original, keyword_candidate_count=0, vector_candidate_count=0,
            excluded_zero_chunk_count=1, fused_candidate_count=0, hits=())
    result = service.build_code_hybrid_context(original)
    assert_package(result)
    assert result.selected_hit_count == (0 if channel == "empty" else 1)


@pytest.mark.parametrize("change", [
    {"rrf_k": 1}, {"strategy": "vector"}, {"fused_candidate_count": 1},
    {"keyword_candidate_count": 0}, {"vector_candidate_count": 0},
    {"excluded_zero_chunk_count": 1}, {"omitted_by_top_k": 1}, {"space_id": "f" * 64},
])
def test_invalid_global_contract(change):
    with pytest.raises(CodeContextError, match="^code_context_snapshot_invalid$"):
        service.build_code_hybrid_context(replace(hybrid(), **change))


@pytest.mark.parametrize("change", [
    {"rrf_score": float("nan")}, {"rrf_score": .99}, {"keyword_rank": 1},
    {"vector_rank": 3}, {"matched_terms": ("unknown",)}, {"keyword_score": 2},
    {"vector_distance": None}, {"vector_distance": -1}, {"rank": 1},
    {"keyword_rank": None}, {"matched_terms": ("sample", "sample")},
])
def test_bad_tail_not_hidden_by_budget(change):
    original = hybrid()
    original = replace(original, hits=(original.hits[0], replace(original.hits[1], **change)))
    with pytest.raises(CodeContextError, match="^code_context_snapshot_invalid$"):
        service.build_code_hybrid_context(original, budget=CodeContextBudget(max_chunks=1))


def test_bad_tail_chunk_and_duplicate_id():
    original = hybrid()
    original.hits[1].chunk["text"] = "corrupted"
    with pytest.raises(CodeContextError, match="code_context_snapshot_invalid"):
        service.build_code_hybrid_context(original, budget=CodeContextBudget(max_chunks=1))
    original = hybrid()
    original = replace(original, hits=(original.hits[0], replace(original.hits[1], chunk=original.hits[0].chunk)))
    with pytest.raises(CodeContextError, match="code_context_snapshot_invalid"):
        service.build_code_hybrid_context(original)


def test_header_budget_invalid_objects_and_snapshot_cap(monkeypatch):
    original = hybrid()
    with pytest.raises(CodeContextError, match="code_context_budget_too_small"):
        service.build_code_hybrid_context(original, budget=CodeContextBudget(max_bytes=1))
    with pytest.raises(CodeContextError, match="code_context_budget_invalid"):
        service.build_code_hybrid_context(original, budget=cast(Any, {}))
    with pytest.raises(CodeContextError, match="code_context_snapshot_invalid"):
        service.build_code_hybrid_context(cast(Any, recall()))
    monkeypatch.setattr(service, "MAX_SNAPSHOT_BYTES", 1)
    with pytest.raises(CodeContextError, match="code_context_snapshot_too_large"):
        service.build_code_hybrid_context(original)


def test_pure_memory_builder(monkeypatch):
    import builtins
    import httpx
    from app.services.workspace.files import code_keyword_search, code_vector_search
    original = hybrid()
    def forbidden(*args, **kwargs):
        raise AssertionError("builder opened IO")
    monkeypatch.setattr(code_keyword_search, "SessionLocal", forbidden)
    monkeypatch.setattr(code_vector_search, "SessionLocal", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    assert json.loads(service.build_code_hybrid_context(original).context_text)["chunks"]


def test_all_twenty_candidates_fit_and_order_is_checked():
    original = hybrid(batch(texts=[f"pass # {i}\n" for i in range(20)]))
    result = service.build_code_hybrid_context(original, budget=CodeContextBudget(max_chunks=20, max_chars=80000, max_bytes=160000))
    assert_package(result)
    assert result.selected_hit_count == 20
    reversed_hits = tuple(replace(hit, rank=index) for index, hit in enumerate(reversed(original.hits), 1))
    with pytest.raises(CodeContextError, match="code_context_snapshot_invalid"):
        service.build_code_hybrid_context(replace(original, hits=reversed_hits))


def test_complete_union_cannot_claim_unobserved_keyword_candidates():
    original = hybrid()
    # 第二个片段只保留向量证据，但谎称关键词仍有2个完整候选。
    tail = replace(original.hits[1], keyword_rank=None, keyword_score=None, matched_terms=(),
                   rrf_score=float(Fraction(1, 62)))
    with pytest.raises(CodeContextError, match="code_context_snapshot_invalid"):
        service.build_code_hybrid_context(replace(original, hits=(original.hits[0], tail)))
