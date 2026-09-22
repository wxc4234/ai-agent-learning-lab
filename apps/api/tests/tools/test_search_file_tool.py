"""搜索工具参数、安全映射及目录发现到行号回答链路。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import ToolErrorObservation, ToolObservation, run_agent_loop
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.services.workspace.files.workspace_listing import WorkspaceDirectoryListing, WorkspaceDirectoryEntry
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.files.workspace_search import WorkspaceSearchError, WorkspaceSearchMatch, WorkspaceSearchResult
from app.tools import search_file as adapter, list_directory
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, model_tools_for_context
from tests.model.test_model_decision import build_text_response, build_tool_response
from tests.tools.test_read_file_tool import CONTEXT


@pytest.mark.parametrize("field,value", [
    ("query", None), ("query", True), ("query", 1), ("query", ""), ("query", "x" * 129),
    ("query", "a\nb"), ("query", "a\rb"), ("query", "a\x00b"),
    ("relative_path", None), ("relative_path", 1), ("relative_path", ""), ("relative_path", "x" * 4097),
])
def test_invalid_parameters(field, value):
    with pytest.raises(ValidationError):
        adapter.SearchTextFileArguments.model_validate({"relative_path": "a", "query": "x", field: value})


@pytest.mark.parametrize("field", ["relative_path", "query"])
def test_both_fields_required(field):
    data = {"relative_path": "a", "query": "x"}
    del data[field]
    with pytest.raises(ValidationError):
        adapter.SearchTextFileArguments.model_validate(data)


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "task_id", "root_path", "context", "regex", "case_sensitive", "recursive", "limit"])
def test_extra_parameters_rejected(field):
    with pytest.raises(ValidationError):
        adapter.SearchTextFileArguments.model_validate({"relative_path": "a", "query": "x", field: True})


@pytest.mark.parametrize("query", [" ", " .* ", "中" * 128])
def test_query_is_preserved(query):
    assert adapter.SearchTextFileArguments(relative_path="a", query=query).query == query


@pytest.mark.parametrize("empty,truncated", [(True, False), (False, False), (False, True)])
def test_identity_result_and_truncation(monkeypatch, empty, truncated):
    calls = []
    matches = () if empty else (WorkspaceSearchMatch(8, 101, "needle", 101, True),)

    def search(**kwargs):
        calls.append(kwargs)
        return WorkspaceSearchResult("notes.txt", "needle", matches, truncated)

    monkeypatch.setattr(adapter, "search_task_text_file", search)
    tool = TOOL_REGISTRY["search_text_file"]
    assert tool.requires_context
    result = json.loads(tool.execute(tool.validate_arguments('{"relative_path":"notes.txt","query":"needle"}'), context=CONTEXT))
    assert calls == [{"user_id": CONTEXT.user_id, "workspace_id": CONTEXT.workspace_id, "task_id": CONTEXT.task_id,
                      "relative_path": "notes.txt", "query": "needle"}]
    assert result == {"relative_path": "notes.txt", "query": "needle", "truncated": truncated,
                      "matches": [] if empty else [{"line_number": 8, "column_number": 101, "snippet": "needle",
                                                    "snippet_start_column": 101, "snippet_truncated": True}]}
    schema = tool.as_model_tool()["function"]["parameters"]
    assert set(schema["required"]) == {"relative_path", "query"}
    assert set(schema["properties"]) == {"relative_path", "query"}


@pytest.mark.parametrize("error,code", [
    (WorkspaceSearchError(), "invalid_search_query"),
    (WorkspaceNotAccessibleError(), "workspace_not_accessible"),
    (WorkspaceDirectoryError("directory_not_found", "PRIVATE"), "workspace_directory_unavailable"),
    (WorkspacePathError("workspace_directory_unbound", "PRIVATE"), "workspace_directory_unbound"),
    (WorkspacePathError("path_outside_workspace", "PRIVATE"), "workspace_path_rejected"),
    *[(WorkspaceFileError(code, "PRIVATE"), code) for code in (
        "file_read_unsupported", "file_not_regular", "file_too_large", "file_not_utf8_text", "file_changed",
    )],
    (WorkspaceFileError("file_access_denied", "PRIVATE"), "file_unavailable"),
])
def test_safe_errors(monkeypatch, error, code):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(adapter, "search_task_text_file", fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.search_text_file(context=CONTEXT, relative_path="a", query="x")
    assert caught.value.code == code
    assert str(caught.value) == SafeToolExecutionError(code).message
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("kind", ["success", "safe", "unknown", "no-context", "invalid"])
def test_model_discovers_searches_and_answers_with_line_number(monkeypatch, kind):
    calls = []

    def listing(**kwargs):
        return WorkspaceDirectoryListing(".", (WorkspaceDirectoryEntry("found.txt", "file"),), False)

    def search(**kwargs):
        calls.append(kwargs)
        if kind == "safe":
            raise WorkspaceFileError("file_too_large", "PRIVATE")
        if kind == "unknown":
            raise RuntimeError("PRIVATE")
        return WorkspaceSearchResult("found.txt", "needle", (WorkspaceSearchMatch(7, 3, "a needle", 1, False),), False)

    provided = None if kind == "no-context" else CONTEXT

    async def create(**kwargs):
        assert kwargs["tools"] == model_tools_for_context(provided)
        messages = [message for message in kwargs["messages"] if message["role"] == "tool"]
        if not messages and kind == "success":
            return build_tool_response(("list", "list_directory", "{}"))
        if not messages or (len(messages) == 1 and kind == "success"):
            name = json.loads(messages[-1]["content"])["entries"][0]["name"] if messages else "found.txt"
            return build_tool_response(("search", "search_text_file", json.dumps({"relative_path": name, "query": "\n" if kind == "invalid" else "needle"})))
        content = json.loads(messages[-1]["content"])
        assert "PRIVATE" not in messages[-1]["content"]
        answer = f'位于第{content["matches"][0]["line_number"]}行' if kind == "success" else "failed"
        return build_text_response(answer)

    monkeypatch.setattr(list_directory, "list_task_directory", listing)
    monkeypatch.setattr(adapter, "search_task_text_file", search)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    maker = DeepSeekDecisionMaker(client=client, model="test", system_prompt="system", user_prompt="search", tool_context=provided)
    result = asyncio.run(run_agent_loop(maker, tool_context=provided))
    if kind == "success":
        assert result.answer == "位于第7行"
        assert all(isinstance(item, ToolObservation) for item in result.observations)
        assert calls[0]["relative_path"] == "found.txt"
    else:
        item = result.observations[-1]
        assert isinstance(item, ToolErrorObservation)
        if kind == "invalid":
            assert item.code == "invalid_tool_arguments"
        else:
            assert item.details == {"safe": "file_too_large", "unknown": "RuntimeError", "no-context": "tool_context_required"}[kind]
    assert len(calls) == (0 if kind in {"invalid", "no-context"} else 1)
