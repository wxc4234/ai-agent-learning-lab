"""组合入口前置校验和阶段装配；没有数据库或真实供应商。"""

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.files import code_retrieval_context as service
from app.services.workspace.files.code_batch_selection import CodeBatchSelectionError, CodeBatchSelectionResult
from app.services.workspace.files.code_context import CodeContextBudget, CodeContextError, build_code_context
from app.services.workspace.files.code_query_context import CodeQueryContextResult
from app.services.workspace.files.code_vector_search import CodeVectorSearchError
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_batch_selection import summary
from tests.workspace.files.test_code_query_context_validation import searched


def invoke(**changes: Any):
    return asyncio.run(service.build_code_retrieval_context(**({
        "query": "  查找取消\n", "user_id": 1, "workspace_id": "a" * 32,
        "task_id": "b" * 32, "config": config(),
    } | changes)))


def selected(found=True):
    return CodeBatchSelectionResult(
        workspace_id="a" * 32, task_id="b" * 32,
        status="selected" if found else "not_found_in_window",
        selected=summary(1) if found else None,
        candidate_count=20, has_more=True,
    )


def forbidden(*args, **kwargs):
    pytest.fail("invalid or unmatched input reached later stage")


@pytest.mark.parametrize("changes,error", [
    ({"query": "  "}, EmbeddingError), ({"query": None}, EmbeddingError),
    ({"query": "x" * 2001}, EmbeddingError), ({"query": "x\x00"}, EmbeddingError),
    ({"top_k": True}, CodeVectorSearchError), ({"top_k": 0}, CodeVectorSearchError),
    ({"top_k": 21}, CodeVectorSearchError), ({"top_k": "5"}, CodeVectorSearchError),
    ({"budget": {}}, CodeContextError),
    ({"budget": CodeContextBudget().model_copy(update={"max_chunks": 0})}, CodeContextError),
    ({"config": None}, CodeBatchSelectionError),
    ({"config": config().model_copy(update={"dimensions": True})}, CodeBatchSelectionError),
])
def test_preflight_before_any_selection(monkeypatch, changes, error):
    monkeypatch.setattr(service, "select_code_embedding_batch", forbidden)
    monkeypatch.setattr(service, "build_code_query_context", forbidden)
    with pytest.raises(error):
        invoke(**changes)


def test_unmatched_preserves_window_without_query(monkeypatch):
    value = selected(False)
    monkeypatch.setattr(service, "select_code_embedding_batch", lambda **kwargs: value)
    monkeypatch.setattr(service, "build_code_query_context", forbidden)
    result = invoke()
    assert result.status == "not_found_in_window" and result.query_context is None
    assert result.selection is value and result.selection.has_more


@pytest.mark.parametrize("changes", [
    {"workspace_id": "f" * 32}, {"task_id": "f" * 32},
    {"status": "not_found_in_window"}, {"selected": None},
    {"selected": summary(1, config(model="other"))},
    {"selected": summary(1, response_model="other-version")},
])
def test_mismatched_selection_never_sends(monkeypatch, changes):
    monkeypatch.setattr(service, "select_code_embedding_batch", lambda **kwargs: replace(selected(), **changes))
    monkeypatch.setattr(service, "build_code_query_context", forbidden)
    with pytest.raises(CodeContextError, match="code_context_selection_invalid"):
        invoke()


def test_selected_parameters_and_budget_copy(monkeypatch):
    value = selected()
    events = []
    active, budget = config(), CodeContextBudget(max_chunks=2)
    search_result = searched()
    sentinel = CodeQueryContextResult(
        query_sha256=search_result.query_sha256,
        requested_model=search_result.requested_model,
        response_model=search_result.response_model,
        prompt_tokens=None, total_tokens=None, request_count=1,
        context=build_code_context(search_result.recall),
    )

    def select(**kwargs):
        assert kwargs == {"user_id": 1, "workspace_id": "a" * 32, "task_id": "b" * 32, "config": active}
        assert kwargs["config"] is not active
        events.append("select")
        return value

    async def query(query, **kwargs):
        assert events == ["select"] and query == "  查找取消\n"
        assert kwargs["batch_id"] == summary(1).batch_id
        assert kwargs["response_model"] == "fixture-model-v1"
        assert kwargs["budget"] == budget and kwargs["budget"] is not budget
        assert kwargs["top_k"] == 3 and kwargs["transport"] is None
        events.append("query")
        return sentinel

    monkeypatch.setattr(service, "select_code_embedding_batch", select)
    monkeypatch.setattr(service, "build_code_query_context", query)
    result = invoke(config=active, budget=budget, top_k=3)
    assert result.status == "context_ready" and result.query_context is sentinel
    assert events == ["select", "query"]


@pytest.mark.parametrize("phase", ["selection", "query"])
def test_error_propagation_without_retry(monkeypatch, phase):
    calls = []
    error = RuntimeError("controlled failure")

    def select(**kwargs):
        calls.append("select")
        if phase == "selection":
            raise error
        return selected()

    async def query(*args, **kwargs):
        calls.append("query")
        raise error

    monkeypatch.setattr(service, "select_code_embedding_batch", select)
    monkeypatch.setattr(service, "build_code_query_context", query)
    with pytest.raises(RuntimeError) as caught:
        invoke()
    assert caught.value is error
    assert calls == (["select"] if phase == "selection" else ["select", "query"])


def test_swallowed_cancel_does_not_publish_result(monkeypatch):
    monkeypatch.setattr(service, "select_code_embedding_batch", lambda **kwargs: selected())

    async def query(*args, **kwargs):
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return

    monkeypatch.setattr(service, "build_code_query_context", query)
    with pytest.raises(asyncio.CancelledError):
        invoke()
