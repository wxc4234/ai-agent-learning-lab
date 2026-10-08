"""上下文串联预检、阶段顺序与未知结果拒绝；不创建数据库。"""

import asyncio
from dataclasses import asdict, replace
from hashlib import sha256
from typing import Any, cast

import pytest

from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.files import code_query_context as service
from app.services.workspace.files.code_context import (
    CodeContextBudget,
    CodeContextError,
)
from app.services.workspace.files.code_query_search import CodeQuerySearchResult
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_context import assert_package, recall


QUERY = "  查找任务取消\n"


def invoke(**changes: Any):
    arguments: dict[str, Any] = {
        "query": QUERY,
        "user_id": 1,
        "workspace_id": "a" * 32,
        "task_id": "b" * 32,
        "batch_id": "c" * 32,
        "config": config(),
        "response_model": "fixture-model-v1",
        "top_k": 2,
        **changes,
    }
    return asyncio.run(service.build_code_query_context(**arguments))


def searched():
    return CodeQuerySearchResult(
        query_sha256=sha256(QUERY.encode()).hexdigest(),
        requested_model="fixture-model",
        response_model="fixture-model-v1",
        prompt_tokens=None,
        total_tokens=None,
        request_count=1,
        recall=recall(),
    )


def forbidden(*args, **kwargs):
    raise AssertionError("rejected input reached a later stage")


@pytest.mark.parametrize("budget", [{}, "PRIVATE_BUDGET", True, 1])
def test_unknown_budget_never_reaches_query_or_builder(monkeypatch, budget):
    monkeypatch.setattr(service, "search_code_query", forbidden)
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(CodeContextError) as caught:
        invoke(budget=budget)
    assert str(caught.value) == "code_context_budget_invalid"


@pytest.mark.parametrize(
    "field,maximum", [("max_chunks", 20), ("max_chars", 80_000), ("max_bytes", 160_000)]
)
@pytest.mark.parametrize("kind", ["zero", "over", "bool", "float", "string", "none"])
def test_bypassed_budget_construction_is_rechecked_before_any_query(
    monkeypatch, capsys, field, maximum, kind
):
    value = {
        "zero": 0,
        "over": maximum + 1,
        "bool": True,
        "float": 1.5,
        "string": "PRIVATE_BUDGET",
        "none": None,
    }[kind]
    # model_copy有意绕过Pydantic构造；生产入口必须重新校验字段而非信任实例。
    budget = CodeContextBudget().model_copy(update={field: value})
    monkeypatch.setattr(service, "search_code_query", forbidden)
    with pytest.raises(CodeContextError) as caught:
        invoke(budget=budget)
    assert str(caught.value) == "code_context_budget_invalid"
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err


@pytest.mark.parametrize(
    "query,code",
    [
        (None, "embedding_query_invalid"),
        (" \n", "embedding_query_invalid"),
        ("PRIVATE\x00QUERY", "embedding_query_invalid"),
        ("x" * 2001, "embedding_query_budget_exceeded"),
    ],
)
def test_query_preflight_does_not_reach_db_http_or_builder(monkeypatch, query, code):
    monkeypatch.setattr(service, "search_code_query", forbidden)
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(EmbeddingError) as caught:
        invoke(query=query)
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "query_usage,code_usage",
    [((None, None), (2, 3)), ((0, 0), (None, None)), ((7, 9), (0, 0))],
)
def test_query_then_builder_uses_copied_budget_and_separates_usage(
    monkeypatch, query_usage, code_usage
):
    value = replace(
        searched(), prompt_tokens=query_usage[0], total_tokens=query_usage[1]
    )
    value.recall.metadata.update(
        prompt_tokens=code_usage[0], total_tokens=code_usage[1], request_count=3
    )
    budget = CodeContextBudget(max_chunks=1)
    events = []
    original_builder = service.build_code_context

    async def search(query, **kwargs):
        assert query == QUERY
        assert kwargs == {
            "user_id": 1,
            "workspace_id": "a" * 32,
            "task_id": "b" * 32,
            "batch_id": "c" * 32,
            "config": config(),
            "response_model": "fixture-model-v1",
            "top_k": 2,
            "transport": None,
        }
        events.append("search")
        return value

    def build(snapshot, *, budget):
        assert events == ["search"] and snapshot is value.recall
        events.append("build")
        return original_builder(snapshot, budget=budget)

    monkeypatch.setattr(service, "search_code_query", search)
    monkeypatch.setattr(service, "build_code_context", build)
    result = invoke(budget=budget)
    assert events == ["search", "build"]
    assert_package(result.context)
    assert result.context.budget == budget and result.context.budget is not budget
    assert (result.prompt_tokens, result.total_tokens) == query_usage
    assert (
        result.context.source_metadata["prompt_tokens"],
        result.context.source_metadata["total_tokens"],
    ) == code_usage
    assert (
        result.request_count == 1
        and result.context.source_metadata["request_count"] == 3
    )
    assert result.query_sha256 == value.query_sha256
    assert (
        result.query_source == "query_embedding"
        and result.source == "code_query_context"
    )
    assert "recall" not in asdict(result) and "query" not in asdict(result)


@pytest.mark.parametrize("value", [None, {}, "PRIVATE_RESULT"])
def test_unknown_search_result_cannot_reach_builder(monkeypatch, value):
    async def search(*args, **kwargs) -> CodeQuerySearchResult:
        return cast(CodeQuerySearchResult, value)

    monkeypatch.setattr(service, "search_code_query", search)
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(CodeContextError) as caught:
        invoke()
    assert str(caught.value) == "code_context_query_result_invalid"


@pytest.mark.parametrize(
    "changes",
    [
        {"query_sha256": "f" * 64},
        {"requested_model": "PRIVATE_MODEL"},
        {"response_model": "fixture-model-v2"},
        {"request_count": True},
        {"request_count": 2},
        {"source": "unknown"},
        {"recall": None},
        {"prompt_tokens": 0},
        {"total_tokens": 0},
        {"prompt_tokens": True, "total_tokens": 1},
        {"prompt_tokens": 2, "total_tokens": 1},
    ],
)
def test_mismatched_query_provenance_and_usage_cannot_reach_builder(
    monkeypatch, changes
):
    async def search(*args, **kwargs):
        return replace(searched(), **changes)

    monkeypatch.setattr(service, "search_code_query", search)
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(CodeContextError) as caught:
        invoke()
    assert str(caught.value) == "code_context_query_result_invalid"


@pytest.mark.parametrize(
    "kind",
    [
        "batch",
        "space",
        "dimensions",
        "top-k",
        "metadata",
        "workspace",
        "task",
        "request-model",
        "response-model",
    ],
)
def test_mismatched_recall_target_cannot_reach_builder(monkeypatch, kind):
    value = searched()
    changes = {
        "batch": {"batch_id": "f" * 32},
        "space": {"space_id": "f" * 64},
        "dimensions": {"dimensions": True},
        "top-k": {"top_k": 1},
        "metadata": {"metadata": None},
    }
    if kind in changes:
        value = replace(value, recall=replace(value.recall, **changes[kind]))
    else:
        field = {
            "workspace": "workspace_id",
            "task": "task_id",
            "request-model": "requested_model",
            "response-model": "response_model",
        }[kind]
        value.recall.metadata[field] = "PRIVATE_OTHER"

    async def search(*args, **kwargs):
        return value

    monkeypatch.setattr(service, "search_code_query", search)
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(CodeContextError) as caught:
        invoke()
    assert str(caught.value) == "code_context_query_result_invalid"


@pytest.mark.parametrize("cancelled", [False, True])
def test_search_failure_or_cancel_is_not_an_empty_context(monkeypatch, cancelled):
    async def search(*args, **kwargs):
        if cancelled:
            raise asyncio.CancelledError()
        raise EmbeddingError("embedding_timeout", "fixed timeout")

    monkeypatch.setattr(service, "search_code_query", search)
    monkeypatch.setattr(service, "build_code_context", forbidden)
    with pytest.raises(asyncio.CancelledError if cancelled else EmbeddingError):
        invoke()


def test_builder_failure_after_search_does_not_retry_relax_or_return_old_result(
    monkeypatch,
):
    events = []

    async def search(*args, **kwargs):
        events.append("search")
        return searched()

    def build(*args, **kwargs):
        events.append("build")
        raise CodeContextError("code_context_snapshot_invalid")

    monkeypatch.setattr(service, "search_code_query", search)
    monkeypatch.setattr(service, "build_code_context", build)
    with pytest.raises(CodeContextError, match="code_context_snapshot_invalid"):
        invoke()
    assert events == ["search", "build"]
