"""纯内存上下文选择、实际JSON预算、来源拒绝与嵌套快照隔离。"""

import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path
from typing import Any, cast

import httpx
from pydantic import ValidationError
import pytest

from app.services.workspace.files import code_context as service
from app.services.workspace.files import code_vector_search, code_vector_storage
from app.services.workspace.files.code_vector_search import (
    CodeVectorHit,
    CodeVectorSearchResult,
)
from app.services.workspace.files.python_chunks import _chunk
from tests.assertions import require_value
from tests.workspace.files.test_code_vector_storage_validation import batch, split_batch


def serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def recall(value=None, *, top_k=None, zeros=0) -> CodeVectorSearchResult:
    value = value or batch(
        texts=["def first(): return 1\n", "def second(): return 2\n"]
    )
    # 使用与数据库JSON一致的数组和普通字典；整个夹具没有数据库依赖。
    metadata = json.loads(serialized(asdict(value)))
    entries = metadata.pop("embeddings")
    top_k = top_k if top_k is not None else len(entries)
    return CodeVectorSearchResult(
        batch_id="c" * 32,
        space_id=require_value(value.embedding_space_id),
        dimensions=value.dimensions,
        top_k=top_k,
        batch_chunk_count=len(entries) + zeros,
        searchable_chunk_count=len(entries),
        excluded_zero_chunk_count=zeros,
        omitted_by_top_k=max(0, len(entries) - top_k),
        metadata=metadata,
        hits=tuple(
            CodeVectorHit(index + 1, index / 20, entry["chunk"])
            for index, entry in enumerate(entries[:top_k])
        ),
    )


def empty_recall() -> CodeVectorSearchResult:
    value = recall()
    return replace(
        value,
        searchable_chunk_count=0,
        excluded_zero_chunk_count=value.batch_chunk_count,
        omitted_by_top_k=0,
        hits=(),
    )


def assert_package(result):
    payload = json.loads(result.context_text)
    assert result.context_chars == len(result.context_text) <= result.budget.max_chars
    assert (
        result.context_bytes
        == len(result.context_text.encode("utf-8"))
        <= result.budget.max_bytes
    )
    assert (
        result.selected_hit_count
        == len(result.selected_chunks)
        <= result.budget.max_chunks
    )
    assert result.input_hit_count == result.selected_hit_count + len(result.omissions)
    assert payload["chunks"] == list(result.selected_chunks)
    assert payload["builder_omitted_hits"] == len(result.omissions)
    assert (
        payload["content_trust"] == result.content_trust == "untrusted_project_content"
    )
    assert payload["scope"] == "selected_chunks_from_one_batch"
    return payload


def assert_invalid(value):
    with pytest.raises(service.CodeContextError) as caught:
        service.build_code_context(
            value, budget=service.CodeContextBudget(max_chunks=1)
        )
    assert str(caught.value) == caught.value.code == "code_context_snapshot_invalid"
    assert "PRIVATE" not in str(caught.value)


def test_default_package_preserves_full_provenance_and_usage_without_sending():
    original = recall()
    before = deepcopy(asdict(original))
    result = service.build_code_context(original)
    payload = assert_package(result)
    assert result.source == "bounded_code_context"
    assert (result.batch_id, result.space_id, result.dimensions) == (
        original.batch_id,
        original.space_id,
        original.dimensions,
    )
    assert result.selected_chunks == tuple(asdict(hit) for hit in original.hits)
    assert result.source_metadata == original.metadata
    assert result.source_metadata["prompt_tokens"] is None
    assert result.source_metadata["total_tokens"] is None
    assert payload["snapshot_truncated"] is False
    assert payload["snapshot_incomplete_reasons"] == []
    assert payload["chunk_policy"] == original.metadata["chunk_policy"]
    assert result.omissions == ()
    assert asdict(original) == before
    assert "EMBED_PRIVATE" not in repr(result) and "provider.invalid" not in repr(
        result
    )


@pytest.mark.parametrize("count", [1, 20])
def test_minimum_and_maximum_candidate_counts_keep_complete_chunks(count):
    value = batch(
        texts=[f"def item_{index}(): return {index}\n" for index in range(count)]
    )
    result = service.build_code_context(
        recall(value),
        budget=service.CodeContextBudget(
            max_chunks=20, max_chars=80_000, max_bytes=160_000
        ),
    )
    assert_package(result)
    assert result.selected_hit_count == count and result.omissions == ()


@pytest.mark.parametrize("limit", [1, 2])
def test_chunk_budget_uses_original_rank_and_records_every_omission(limit):
    value = recall(
        batch(texts=[f"def item_{index}(): return {index}\n" for index in range(3)])
    )
    result = service.build_code_context(
        value, budget=service.CodeContextBudget(max_chunks=limit)
    )
    assert_package(result)
    assert [entry["rank"] for entry in result.selected_chunks] == list(
        range(1, limit + 1)
    )
    assert [item.source_rank for item in result.omissions] == list(range(limit + 1, 4))
    assert all(item.reasons == ("chunk_budget",) for item in result.omissions)
    assert [item.chunk_id for item in result.omissions] == [
        hit.chunk["chunk_id"] for hit in value.hits[limit:]
    ]


@pytest.mark.parametrize(
    "field,reason", [("max_chars", "character_budget"), ("max_bytes", "byte_budget")]
)
def test_actual_rendered_budget_accepts_exact_size_and_omits_one_over(field, reason):
    value = recall(batch(texts=["def sample(): return '中文'\n"]))
    full = service.build_code_context(value)
    size = full.context_chars if field == "max_chars" else full.context_bytes
    exact = service.build_code_context(
        value, budget=service.CodeContextBudget(**{field: size})
    )
    assert_package(exact)
    assert exact.context_text == full.context_text
    reduced = service.build_code_context(
        value, budget=service.CodeContextBudget(**{field: size - 1})
    )
    assert_package(reduced)
    assert reduced.selected_chunks == ()
    assert reduced.omissions[0].reasons == (reason,)
    assert value.hits[0].chunk["text"] == "def sample(): return '中文'\n"


def test_unicode_bytes_and_json_escaping_are_measured_in_the_actual_text():
    value = recall(batch(texts=['def sample(): return "中文\\路径"\n']))
    full = service.build_code_context(value)
    assert full.context_bytes > full.context_chars
    assert "\\n" in full.context_text and '\\"' in full.context_text
    assert len(full.context_text) > len(value.hits[0].chunk["text"])
    byte_limited = service.build_code_context(
        value,
        budget=service.CodeContextBudget(
            max_chars=full.context_chars, max_bytes=full.context_chars
        ),
    )
    assert_package(byte_limited)
    assert byte_limited.omissions[0].reasons == ("byte_budget",)
    both = service.build_code_context(
        value,
        budget=service.CodeContextBudget(
            max_chars=full.context_chars - 1, max_bytes=full.context_bytes - 1
        ),
    )
    assert_package(both)
    assert both.omissions[0].reasons == ("character_budget", "byte_budget")


def test_large_first_candidate_does_not_prevent_a_later_small_complete_chunk():
    value = recall(batch(texts=["x" * 1600, "def small(): return 2\n"]))
    full = service.build_code_context(value)
    expected = json.loads(full.context_text)
    expected["chunks"] = [expected["chunks"][1]]
    expected["builder_omitted_hits"] = 1
    expected_text = serialized(expected)
    result = service.build_code_context(
        value, budget=service.CodeContextBudget(max_chars=len(expected_text))
    )
    assert_package(result)
    assert result.context_text == expected_text
    assert result.selected_chunks[0]["rank"] == 2
    assert result.selected_chunks[0]["chunk"]["text"] == value.hits[1].chunk["text"]
    assert result.omissions[0].source_rank == 1
    assert result.omissions[0].reasons == ("character_budget",)


def duplicate_recall():
    value = recall()
    first = value.hits[0]
    return replace(
        value, hits=(first, replace(first, rank=2, chunk=deepcopy(first.chunk)))
    )


def test_identical_duplicate_keeps_first_rank_and_precedes_chunk_budget():
    value = duplicate_recall()
    result = service.build_code_context(
        value, budget=service.CodeContextBudget(max_chunks=1)
    )
    assert_package(result)
    assert result.selected_hit_count == 1 and result.selected_chunks[0]["rank"] == 1
    assert result.omissions[0].reasons == ("duplicate_chunk",)


def test_duplicate_of_a_rejected_first_chunk_is_not_retried():
    value = duplicate_recall()
    full = service.build_code_context(value)
    result = service.build_code_context(
        value, budget=service.CodeContextBudget(max_chars=full.context_chars - 1)
    )
    assert_package(result)
    assert result.selected_chunks == ()
    assert [item.reasons for item in result.omissions] == [
        ("character_budget",),
        ("duplicate_chunk",),
    ]


@pytest.mark.parametrize("kind", ["distance", "part-count"])
def test_conflicting_duplicate_outside_selection_budget_rejects_everything(kind):
    value = duplicate_recall()
    second = value.hits[1]
    if kind == "distance":
        second = replace(second, distance=0.1)
    else:
        second = replace(second, chunk={**second.chunk, "part_count": 2})
    assert_invalid(replace(value, hits=(value.hits[0], second)))


def test_identical_text_in_different_files_keeps_both_sources():
    value = batch(texts=["def sample(): return 1\n"])
    first = value.embeddings[0]
    other_file = replace(value.files[0], relative_path="other.py")
    symbol = replace(first.chunk.symbol, relative_path=other_file.relative_path)
    identity = _chunk(
        symbol,
        first.chunk.text,
        first.chunk.start_line,
        first.chunk.start_column,
        first.chunk.end_line,
        first.chunk.end_column,
        first.chunk.part_index,
    ).chunk_id
    other = replace(first, chunk=replace(first.chunk, symbol=symbol, chunk_id=identity))
    result = service.build_code_context(
        recall(
            replace(value, files=(*value.files, other_file), embeddings=(first, other))
        )
    )
    assert_package(result)
    assert result.selected_hit_count == 2 and result.omissions == ()
    assert {
        item["chunk"]["symbol"]["relative_path"] for item in result.selected_chunks
    } == {"fixture.py", "other.py"}


def test_generation_recall_and_builder_coverage_remain_separate():
    value = replace(
        batch(texts=[f"def item_{index}(): return {index}\n" for index in range(4)]),
        truncated=True,
        incomplete_reasons=("chunk_budget",),
    )
    result = service.build_code_context(
        recall(value, top_k=3), budget=service.CodeContextBudget(max_chunks=1)
    )
    payload = assert_package(result)
    assert payload["snapshot_truncated"] is True
    assert payload["snapshot_incomplete_reasons"] == ["chunk_budget"]
    assert (
        payload["recall_omitted_by_top_k"] == 1 and payload["builder_omitted_hits"] == 2
    )
    assert result.source_metadata["truncated"] is True
    assert result.recall_summary == {
        "batch_chunk_count": 4,
        "searchable_chunk_count": 4,
        "excluded_zero_chunk_count": 0,
        "omitted_by_top_k": 1,
    }


def test_selected_definition_part_does_not_require_unreturned_parts():
    result = service.build_code_context(recall(split_batch(), top_k=1))
    assert_package(result)
    assert result.source_metadata["truncated"] is False
    assert result.selected_chunks[0]["chunk"]["part_count"] == 2
    assert result.selected_chunks[0]["chunk"]["part_index"] == 1
    assert result.recall_summary["omitted_by_top_k"] == 1


def test_zero_searchable_candidates_returns_bounded_empty_context_with_audit():
    result = service.build_code_context(empty_recall())
    payload = assert_package(result)
    assert result.input_hit_count == result.selected_hit_count == 0
    assert result.selected_chunks == result.omissions == ()
    assert payload["chunks"] == [] and result.context_chars > 0
    assert result.recall_summary["excluded_zero_chunk_count"] == 2


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("field", ["max_chars", "max_bytes"])
def test_source_header_has_an_exact_budget_and_cannot_be_silently_removed(empty, field):
    value = empty_recall() if empty else recall()
    # 只构造公开输出协议的空片段形式，预算仍包含来源与覆盖声明。
    header = json.loads(service.build_code_context(value).context_text)
    header["chunks"] = []
    header["builder_omitted_hits"] = len(value.hits)
    text = serialized(header)
    size = len(text) if field == "max_chars" else len(text.encode())
    exact = service.build_code_context(
        value, budget=service.CodeContextBudget(**{field: size})
    )
    assert_package(exact)
    assert exact.selected_chunks == () and exact.context_text == text
    with pytest.raises(service.CodeContextError) as caught:
        service.build_code_context(
            value, budget=service.CodeContextBudget(**{field: size - 1})
        )
    assert str(caught.value) == "code_context_budget_too_small"


@pytest.mark.parametrize(
    "field,maximum", [("max_chunks", 20), ("max_chars", 80_000), ("max_bytes", 160_000)]
)
@pytest.mark.parametrize(
    "kind", ["zero", "negative", "over", "bool", "float", "string", "none"]
)
def test_budget_constructor_rejects_invalid_strict_scalars(field, maximum, kind):
    value = {
        "zero": 0,
        "negative": -1,
        "over": maximum + 1,
        "bool": True,
        "float": 1.5,
        "string": "1",
        "none": None,
    }[kind]
    with pytest.raises(ValidationError):
        service.CodeContextBudget.model_validate({field: value})


@pytest.mark.parametrize("value", [{}, "PRIVATE_BUDGET", 1, True])
def test_unknown_budget_object_is_static(value: object):
    with pytest.raises(service.CodeContextError) as caught:
        service.build_code_context(
            recall(), budget=cast(service.CodeContextBudget, value)
        )
    assert str(caught.value) == "code_context_budget_invalid"


@pytest.mark.parametrize("value", [None, {}, [], "PRIVATE_RESULT"])
def test_unknown_snapshot_object_is_static(value: object):
    assert_invalid(cast(CodeVectorSearchResult, value))


def test_snapshot_byte_cap_accepts_exact_serialization_then_rejects_one_less(
    monkeypatch,
):
    value = recall(batch(texts=["def sample(): return '中文'\n"]))
    size = len(serialized(asdict(value)).encode())
    monkeypatch.setattr(service, "MAX_SNAPSHOT_BYTES", size)
    assert_package(service.build_code_context(value))
    monkeypatch.setattr(service, "MAX_SNAPSHOT_BYTES", size - 1)
    with pytest.raises(service.CodeContextError) as caught:
        service.build_code_context(value)
    assert str(caught.value) == "code_context_snapshot_too_large"


def test_actual_oversized_snapshot_is_rejected_before_schema_validation():
    value = recall()
    value.metadata["PRIVATE_EXTRA"] = "x" * service.MAX_SNAPSHOT_BYTES
    with pytest.raises(service.CodeContextError) as caught:
        service.build_code_context(value)
    assert str(caught.value) == "code_context_snapshot_too_large"


@pytest.mark.parametrize(
    "changes",
    [
        {"batch_id": ""},
        {"batch_id": "x" * 101},
        {"dimensions": True},
        {"dimensions": 2},
        {"space_id": "f" * 64},
        {"space_id": "PRIVATE"},
        {"top_k": 0},
        {"top_k": 21},
        {"top_k": True},
        {"top_k": 1},
        {"batch_chunk_count": 0},
        {"batch_chunk_count": 21},
        {"searchable_chunk_count": 1},
        {"excluded_zero_chunk_count": 1},
        {"omitted_by_top_k": 1},
        {"hits": ()},
    ],
)
def test_inconsistent_scope_counts_and_space_never_return_partial_context(changes):
    assert_invalid(replace(recall(), **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"PRIVATE_EXTRA": "PRIVATE_CONFIG"},
        {"files": []},
        {"dimensions": 2},
        {"embedding_space_id": "f" * 64},
        {"truncated": None},
        {"truncated": True},
        {"incomplete_reasons": ["unknown"]},
        {"truncated": True, "incomplete_reasons": ["chunk_budget", "chunk_budget"]},
        {"prompt_tokens": 0},
        {"total_tokens": 0},
        {"prompt_tokens": True, "total_tokens": 1},
        {"prompt_tokens": 2, "total_tokens": 1},
        {"requested_model": " PRIVATE "},
        {"response_model": "PRIVATE\x00MODEL"},
        {"chunk_parser": "x" * 101},
        {"chunk_policy": "unknown"},
        {"source": "unknown"},
        {"content_trust": "trusted"},
    ],
)
def test_invalid_metadata_is_not_an_invented_safe_state(changes):
    value = recall()
    value.metadata.update(changes)
    assert_invalid(value)


@pytest.mark.parametrize("usage", [(None, None), (0, 0), (2, 3)])
def test_unknown_zero_and_reported_usage_remain_distinct(usage):
    value = recall()
    value.metadata.update(prompt_tokens=usage[0], total_tokens=usage[1])
    result = service.build_code_context(value)
    assert (
        result.source_metadata["prompt_tokens"],
        result.source_metadata["total_tokens"],
    ) == usage


@pytest.mark.parametrize("kind", ["duplicate", "digest", "size", "absolute-path"])
def test_file_provenance_corruption_fails_before_selection(kind):
    value = recall()
    file = value.metadata["files"][0]
    if kind == "duplicate":
        value.metadata["files"].append(deepcopy(file))
    elif kind == "digest":
        file["sha256"] = "f" * 64
    elif kind == "size":
        file["byte_count"] = 0
    else:
        file["relative_path"] = "/PRIVATE/source.py"
    assert_invalid(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"chunk_id": "f" * 64},
        {"text_sha256": "f" * 64},
        {"text": "PRIVATE_REPLACED"},
        {"text": "PRIVATE\x00TEXT"},
        {"text": "\ud800"},
        {"text": "x" * 2001},
        {"start_column": 0},
        {"end_column": 2},
        {"line_count": 20},
        {"part_index": 0},
        {"part_index": 2},
        {"split_reasons": ["unknown"]},
        {"PRIVATE_EXTRA": "PRIVATE_BODY"},
    ],
)
def test_invalid_chunk_outside_max_chunks_is_not_hidden(changes):
    value = recall()
    second = value.hits[1]
    second.chunk.update(changes)
    assert_invalid(value)


@pytest.mark.parametrize(
    "distance", [None, True, "PRIVATE_DISTANCE", float("nan"), float("inf"), -0.1, 2.1]
)
def test_unknown_or_invalid_tail_distance_is_not_omitted_as_low_relevance(distance):
    value = recall()
    assert_invalid(
        replace(value, hits=(value.hits[0], replace(value.hits[1], distance=distance)))
    )


@pytest.mark.parametrize(
    "kind", ["rank", "descending", "missing-file", "symbol-digest"]
)
def test_rank_order_and_symbol_source_must_match_the_snapshot(kind):
    value = recall()
    second = value.hits[1]
    if kind == "rank":
        value = replace(value, hits=(value.hits[0], replace(second, rank=1)))
    elif kind == "descending":
        value = replace(value, hits=(replace(value.hits[0], distance=1.0), second))
    else:
        second.chunk["symbol"][
            "relative_path" if kind == "missing-file" else "sha256"
        ] = "missing.py" if kind == "missing-file" else "f" * 64
    assert_invalid(value)


@pytest.mark.parametrize("kind", ["unsupported-object", "circular"])
def test_unserializable_snapshot_is_static_and_does_not_leak(kind):
    value = recall()
    value.metadata["PRIVATE_EXTRA"] = (
        object() if kind == "unsupported-object" else value.metadata
    )
    assert_invalid(value)


def test_nested_results_and_text_do_not_alias_source_or_other_builds():
    value = recall()
    before = deepcopy(asdict(value))
    first = service.build_code_context(value)
    second = service.build_code_context(value)
    original_text = first.context_text
    first.selected_chunks[0]["chunk"]["symbol"]["name"] = "changed"
    first.source_metadata["files"][0]["relative_path"] = "changed.py"
    first.recall_summary["batch_chunk_count"] = 99
    assert asdict(value) == before
    assert second.selected_chunks[0]["chunk"]["symbol"]["name"] != "changed"
    assert second.source_metadata["files"][0]["relative_path"] == "fixture.py"
    assert first.context_text == second.context_text == original_text
    value.hits[0].chunk["text"] = "changed source after build"
    assert second.selected_chunks[0]["chunk"]["text"] != value.hits[0].chunk["text"]


def test_package_and_budget_are_frozen_but_audit_dicts_are_independent_snapshots():
    result = service.build_code_context(recall())
    with pytest.raises(FrozenInstanceError):
        setattr(result, "context_text", "changed")  # noqa: B010 -- 验证冻结字段的运行时约束
    with pytest.raises(ValidationError):
        setattr(result.budget, "max_chunks", 20)  # noqa: B010 -- 验证冻结预算的运行时约束


def test_builder_does_not_open_session_http_client_or_project_files(monkeypatch):
    value = recall()

    def forbidden(*args, **kwargs):
        raise AssertionError("pure context builder reached I/O")

    # 只在构建调用范围内封住I/O，避免影响pytest自身的文件管理。
    with monkeypatch.context() as patcher:
        patcher.setattr(code_vector_search, "SessionLocal", None)
        patcher.setattr(code_vector_storage, "SessionLocal", None)
        patcher.setattr(httpx, "AsyncClient", forbidden)
        patcher.setattr(builtins, "open", forbidden)
        patcher.setattr(Path, "read_text", forbidden)
        patcher.setattr(Path, "read_bytes", forbidden)
        result = service.build_code_context(value)
    assert_package(result)
