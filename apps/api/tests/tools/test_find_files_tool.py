"""文件查找工具：参数边界、安全错误及请求能力下的查找到读取链路。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import ToolErrorObservation, ToolObservation, run_agent_loop
from app.services.workspace.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.workspace_file import WorkspaceTextFile
from app.services.workspace.workspace_find import WorkspaceFindError, WorkspaceFindResult
from app.services.workspace.workspace_path import WorkspacePathError
from app.tools import find_files as adapter, read_file
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError, model_tools_for_context, tools_for_execution
from tests.model.test_model_decision import build_text_response, build_tool_response
from tests.tools.test_read_file_tool import CONTEXT


@pytest.mark.parametrize("field,value", [
    ("query", None), ("query", True), ("query", 1), ("query", ""), ("query", "x" * 129),
    ("query", "a\nb"), ("query", "a\rb"), ("query", "a\x00b"), ("query", "a/b"), ("query", "a\\b"),
    ("relative_path", None), ("relative_path", True), ("relative_path", 1),
    ("relative_path", ""), ("relative_path", "x" * 4097),
])
def test_invalid_parameters(field, value):
    with pytest.raises(ValidationError):
        TOOL_REGISTRY["find_files"].validate_arguments(json.dumps({"query": "x", field: value}))


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "task_id", "conversation_id", "root_path", "context", "max_depth", "limit", "regex"])
def test_extra_parameters_rejected(field):
    with pytest.raises(ValidationError):
        adapter.FindFilesArguments.model_validate({"query": "x", field: "forged"})


@pytest.mark.parametrize("query", [" ", " .* ", "中" * 128])
def test_preserved_query_default_and_schema(query):
    arguments = adapter.FindFilesArguments(query=query)
    assert arguments.query == query and arguments.relative_path == "."
    schema = TOOL_REGISTRY["find_files"].as_model_tool()["function"]["parameters"]
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "relative_path"}
    assert schema["additionalProperties"] is False
    with pytest.raises(ValidationError):
        adapter.FindFilesArguments.model_validate({})


@pytest.mark.parametrize("empty,truncated", [(True, False), (True, True), (False, False), (False, True)])
def test_identity_and_result_contract(monkeypatch, empty, truncated):
    calls = []
    paths = () if empty else ("src/中文.py",)

    def find(**kwargs):
        calls.append(kwargs)
        return WorkspaceFindResult("src", "中文", paths, 2000, truncated)

    monkeypatch.setattr(adapter, "find_task_files", find)
    tool = TOOL_REGISTRY["find_files"]
    raw = tool.execute(tool.validate_arguments('{"relative_path":"src","query":"中文"}'), context=CONTEXT)
    assert json.loads(raw) == {"relative_path": "src", "query": "中文", "paths": list(paths),
                               "scanned_entries": 2000, "truncated": truncated}
    assert "中文" in raw
    assert calls == [{"user_id": CONTEXT.user_id, "workspace_id": CONTEXT.workspace_id,
                      "task_id": CONTEXT.task_id, "relative_path": "src", "query": "中文"}]


@pytest.mark.parametrize("error,code", [
    (WorkspaceNotAccessibleError(), "workspace_not_accessible"),
    (WorkspaceDirectoryError("directory_not_found", "PRIVATE"), "workspace_directory_unavailable"),
    (WorkspacePathError("workspace_directory_unbound", "PRIVATE"), "workspace_directory_unbound"),
    (WorkspacePathError("path_outside_workspace", "PRIVATE"), "workspace_path_rejected"),
    *[(WorkspaceFindError(code, "PRIVATE"), code) for code in (
        "invalid_find_query", "file_find_unsupported", "file_find_changed",
        "file_find_access_denied", "file_find_unavailable",
    )],
    (WorkspaceFindError("future_code", "PRIVATE"), "file_find_unavailable"),
])
def test_safe_errors(monkeypatch, error, code):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(adapter, "find_task_files", fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.find_files(context=CONTEXT, query="x")
    assert caught.value.code == code
    assert str(caught.value) == SafeToolExecutionError(code).message
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("context", [None, {}, "forged"])
def test_missing_context_refused_before_service(monkeypatch, context):
    monkeypatch.setattr(adapter, "find_task_files", lambda **kwargs: pytest.fail("must not execute"))
    tool = TOOL_REGISTRY["find_files"]
    with pytest.raises(ToolContextRequiredError):
        tool.execute(tool.validate_arguments('{"query":"x"}'), context=context)
    assert "find_files" not in {item.name for item in tools_for_execution(context=context)}
    assert "find_files" not in {item["function"]["name"] for item in model_tools_for_context(context)}


def test_request_registration_preserves_existing_capabilities():
    definitions = tools_for_execution(context=CONTEXT)
    assert sum(tool.name == "find_files" for tool in definitions) == 1
    assert {"read_text_file", "search_text_file", "list_directory", "get_current_time"} <= {tool.name for tool in definitions}
    assert TOOL_REGISTRY["find_files"] in definitions
    assert TOOL_REGISTRY["find_files"].requires_context
    assert not TOOL_REGISTRY["find_files"].is_async
    assert TOOL_REGISTRY["find_files"].timeout_seconds > 0


@pytest.mark.parametrize("error", [RuntimeError("PRIVATE"), asyncio.CancelledError()])
def test_unclassified_exception_or_cancel_propagates(monkeypatch, error):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(adapter, "find_task_files", fail)
    with pytest.raises(type(error)) as caught:
        adapter.find_files(context=CONTEXT, query="x")
    assert caught.value is error


@pytest.mark.parametrize("kind", ["success", "empty", "truncated", "safe", "unknown", "invalid", "no-context"])
def test_request_model_finds_then_reads_or_handles_error(monkeypatch, kind):
    calls, reads = [], []
    provided = None if kind == "no-context" else CONTEXT
    definitions = tools_for_execution(context=provided)

    def find(**kwargs):
        calls.append(kwargs)
        if kind == "safe":
            raise WorkspaceFindError("file_find_changed", "PRIVATE")
        if kind == "unknown":
            raise RuntimeError("PRIVATE")
        return WorkspaceFindResult(".", "found", () if kind in {"empty", "truncated"} else ("src/found.txt",), 10, kind == "truncated")

    def read(**kwargs):
        reads.append(kwargs)
        assert kwargs["relative_path"] == "src/found.txt"
        assert kwargs["user_id"] == CONTEXT.user_id
        return WorkspaceTextFile("src/found.txt", "located content", 15)

    async def create(**kwargs):
        assert kwargs["tools"] == [tool.as_model_tool() for tool in definitions]
        messages = [message for message in kwargs["messages"] if message["role"] == "tool"]
        if not messages:
            return build_tool_response(("find", "find_files", json.dumps({"query": "\n" if kind == "invalid" else "found"})))
        assert "PRIVATE" not in messages[-1]["content"]
        content = json.loads(messages[-1]["content"])
        if kind == "success" and len(messages) == 1:
            return build_tool_response(("read", "read_text_file", json.dumps({"relative_path": content["paths"][0]})))
        if kind == "success":
            assert content["content"] == "located content"
        if kind in {"empty", "truncated"}:
            assert content["paths"] == []
            assert content["truncated"] is (kind == "truncated")
        return build_text_response("done")

    monkeypatch.setattr(adapter, "find_task_files", find)
    monkeypatch.setattr(read_file, "read_task_text_file", read)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    maker = DeepSeekDecisionMaker(client=client, model="test", system_prompt="system", user_prompt="find",
                                 tool_context=provided, tool_definitions=definitions)
    result = asyncio.run(run_agent_loop(maker, tool_context=provided, tool_definitions=definitions))
    assert result.answer == "done"
    assert len(calls) == (0 if kind in {"invalid", "no-context"} else 1)
    assert len(reads) == (1 if kind == "success" else 0)
    if kind in {"success", "empty", "truncated"}:
        assert all(isinstance(item, ToolObservation) for item in result.observations)
    else:
        item = result.observations[0]
        assert isinstance(item, ToolErrorObservation)
        if kind == "invalid":
            assert item.code == "invalid_tool_arguments"
        elif kind == "no-context":
            assert item.code == "unknown_tool"
        else:
            assert item.details == {"safe": "file_find_changed", "unknown": "RuntimeError"}[kind]
