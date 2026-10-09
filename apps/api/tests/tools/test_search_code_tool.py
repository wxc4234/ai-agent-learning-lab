"""未注册工具定义、参数和投影；受控组合结果，无供应商或数据库。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.embedding_config import EmbeddingError
from app.services.workspace.files.code_batch_selection import CodeBatchSelectionResult
from app.services.workspace.files.code_batch_summaries import CodeBatchSummaryError
from app.services.workspace.files.code_context import CodeContextError, build_code_context
from app.services.workspace.files.code_query_context import CodeQueryContextResult
from app.services.workspace.files.code_retrieval_context import CodeRetrievalContextResult
from app.services.workspace.files.code_vector_search import CodeVectorSearchError
from app.tools import search_code as tool
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError
from tests.assertions import require_value
from tests.model.test_code_embeddings import config
from tests.workspace.files.test_code_batch_selection import summary
from tests.workspace.files.test_code_context import recall, empty_recall

CONTEXT = ToolExecutionContext(1, "d" * 32, "a" * 32, "b" * 32)
QUERY = "  查找取消\n"


def result(*, found=True, empty=False):
    snapshot = empty_recall() if empty else recall()
    package = build_code_context(snapshot)
    selection = CodeBatchSelectionResult(
        workspace_id=CONTEXT.workspace_id, task_id=CONTEXT.task_id,
        status="selected" if found else "not_found_in_window",
        selected=summary(1, batch_id=package.batch_id, chunk_count=snapshot.batch_chunk_count) if found else None,
        candidate_count=20, has_more=True,
    )
    query = CodeQueryContextResult(
        query_sha256=sha256(QUERY.encode()).hexdigest(), requested_model=config().model,
        response_model="fixture-model-v1", prompt_tokens=None, total_tokens=None,
        request_count=1, context=package,
    ) if found else None
    return CodeRetrievalContextResult(status="context_ready" if found else "not_found_in_window", selection=selection, query_context=query)


def install(monkeypatch, value=None, error=None):
    calls = []

    async def retrieve(query, **kwargs):
        calls.append((query, kwargs))
        if error is not None:
            raise error
        return value if value is not None else result()

    monkeypatch.setattr(tool, "build_code_retrieval_context", retrieve)
    return calls


def invoke(definition=None, query=QUERY):
    definition = definition or tool.make_search_code_definition(config=config())
    arguments = definition.validate_arguments(json.dumps({"query": query}))
    return asyncio.run(definition.execute_async(arguments, context=CONTEXT))


@pytest.mark.parametrize("arguments", [
    {}, {"query": None}, {"query": 1}, {"query": " \n"}, {"query": "a\x00"},
    {"query": "x" * 2001}, {"query": "中" * 1366}, {"query": "\ud800"},
    *({"query": "valid", key: "PRIVATE"} for key in (
        "context", "user_id", "workspace_id", "task_id", "conversation_id", "batch_id",
        "response_model", "config", "api_key", "base_url", "top_k", "budget", "transport", "root_path",
    )),
])
def test_strict_query_only_arguments(arguments):
    with pytest.raises(ValidationError):
        tool.SearchCodeArguments.model_validate(arguments)


def test_definition_injection_and_no_registration(monkeypatch):
    calls = install(monkeypatch)
    definition = tool.make_search_code_definition(config=config())
    schema = tool.SearchCodeArguments.model_json_schema()
    assert definition.as_model_tool()["function"].get("parameters") == schema
    assert schema["additionalProperties"] is False and set(schema["properties"]) == {"query"}
    assert definition.name not in TOOL_REGISTRY and definition.is_async and definition.requires_context
    assert definition.timeout_seconds > 0
    payload = json.loads(invoke(definition))
    assert payload["status"] == "context_ready"
    query, kwargs = calls[0]
    assert query == QUERY and kwargs["user_id"] == CONTEXT.user_id
    assert kwargs["workspace_id"] == CONTEXT.workspace_id and kwargs["task_id"] == CONTEXT.task_id
    assert kwargs["config"] == config() and kwargs["top_k"] == 5
    with pytest.raises(ToolContextRequiredError):
        asyncio.run(definition.execute_async(tool.SearchCodeArguments(query=QUERY)))
    async def direct_invalid():
        return await require_value(definition.async_executor)(context=CONTEXT, query=QUERY, batch_id="PRIVATE")

    with pytest.raises(SafeToolExecutionError):
        asyncio.run(direct_invalid())
    assert len(calls) == 1


@pytest.mark.parametrize("found,empty", [(False, False), (True, True), (True, False)])
def test_projection_success_and_empty_are_distinct(monkeypatch, found, empty):
    install(monkeypatch, result(found=found, empty=empty))
    raw = invoke()
    payload = json.loads(raw)
    assert set(payload) == {"source", "content_trust", "scope", "status", "search_window", "coverage", "matches"}
    assert payload["search_window"] == {"candidate_count": 20, "has_more": True, "limit": 20}
    assert payload["status"] == ("context_ready" if found else "not_found_in_window")
    assert (payload["coverage"] is None) is (not found)
    if found and not empty:
        assert len(payload["matches"]) == 2
        assert set(payload["matches"][0]) == {"relative_path", "symbol", "start_line", "start_column", "end_line", "end_column", "text"}
        assert payload["matches"][0]["text"] == "def first(): return 1\n"
    else:
        assert payload["matches"] == []
    for private in ("batch_id", "space_id", "dimensions", "prompt_tokens", "api_key", "sha256", "distance", "provider.invalid"):
        assert private not in raw


@pytest.mark.parametrize("error,code", [
    (WorkspaceNotAccessibleError(), "workspace_not_accessible"),
    (CodeBatchSummaryError("code_embedding_project_unbound"), "workspace_directory_unbound"),
    (CodeBatchSummaryError("PRIVATE"), "code_search_unavailable"),
    (EmbeddingError("embedding_timeout", "PRIVATE"), "code_search_timeout"),
    (EmbeddingError("PRIVATE", "PRIVATE"), "code_search_unavailable"),
    (CodeVectorSearchError("code_embedding_query_result_invalid"), "code_search_space_changed"),
    (CodeVectorSearchError("PRIVATE"), "code_search_unavailable"),
    (CodeContextError("code_context_budget_too_small"), "code_search_budget_exceeded"),
    (CodeContextError("PRIVATE"), "code_search_unavailable"),
    (RuntimeError("PRIVATE key/path/sql"), "code_search_unavailable"),
])
def test_fixed_error_mapping_no_retry(monkeypatch, error, code):
    calls = install(monkeypatch, error=error)
    with pytest.raises(SafeToolExecutionError) as caught:
        invoke()
    assert caught.value.code == code and "PRIVATE" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("swallowed", [False, True])
def test_cancellation_is_not_observation(monkeypatch, swallowed):
    async def retrieve(*args, **kwargs):
        if not swallowed:
            raise asyncio.CancelledError()
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return result()

    monkeypatch.setattr(tool, "build_code_retrieval_context", retrieve)
    with pytest.raises(asyncio.CancelledError):
        invoke()


@pytest.mark.parametrize("kind", ["target", "query", "space", "version", "path", "text", "count", "extra", "status", "window"])
def test_invalid_provenance_and_unknown_fields_rejected(monkeypatch, kind):
    value = result()
    queried = require_value(value.query_context)
    package = queried.context
    if kind == "target":
        value = replace(value, selection=replace(value.selection, task_id="f" * 32))
    elif kind == "query":
        value = replace(value, query_context=replace(queried, query_sha256="f" * 64))
    elif kind == "space":
        value = replace(value, query_context=replace(queried, context=replace(package, space_id="f" * 64)))
    elif kind == "version":
        value = replace(value, query_context=replace(queried, response_model="PRIVATE"))
    elif kind == "count":
        value = replace(value, query_context=replace(queried, context=replace(package, selected_hit_count=True)))
    elif kind == "status":
        value = replace(value, status="not_found_in_window")
    elif kind == "window":
        value = replace(value, selection=replace(value.selection, candidate_count=1))
    else:
        chunks = deepcopy(package.selected_chunks)
        if kind == "path":
            chunks[0]["chunk"]["symbol"]["relative_path"] = "/PRIVATE"
        elif kind == "text":
            chunks[0]["chunk"]["text"] = "PRIVATE"
        else:
            chunks[0]["PRIVATE"] = "PRIVATE"
        value = replace(value, query_context=replace(queried, context=replace(package, selected_chunks=chunks)))
    install(monkeypatch, value)
    with pytest.raises(SafeToolExecutionError) as caught:
        invoke()
    assert caught.value.code == "code_search_unavailable" and "PRIVATE" not in str(caught.value)


def test_actual_utf8_output_limit_exact_and_one_byte_over(monkeypatch):
    install(monkeypatch)
    expected = invoke()
    size = len(expected.encode("utf-8"))
    monkeypatch.setattr(tool, "MAX_CODE_TOOL_RESULT_BYTES", size)
    assert invoke() == expected
    monkeypatch.setattr(tool, "MAX_CODE_TOOL_RESULT_BYTES", size - 1)
    with pytest.raises(SafeToolExecutionError) as caught:
        invoke()
    assert caught.value.code == "code_search_result_too_large"


def test_snapshot_limit_is_failure_not_partial(monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr(tool, "MAX_CODE_TOOL_SNAPSHOT_BYTES", 1)
    with pytest.raises(SafeToolExecutionError, match="无法确认"):
        invoke()
