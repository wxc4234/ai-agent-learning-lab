"""文件工具适配、固定错误契约及模型能力过滤；不访问数据库或模型。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime import agent_runtime as runtime
from app.services.workspace.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.workspace_file import WorkspaceFileError, WorkspaceTextFile
from app.services.workspace.workspace_path import WorkspacePathError
from app.tools import read_file as adapter
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, TOOLS, model_tools_for_context
from tests.model.test_model_decision import build_text_response, build_tool_response


CONTEXT = ToolExecutionContext(1, "private-conversation", "private-workspace", "private-task")


@pytest.mark.parametrize("value", [None, 1, True, "", "x" * 4097, [], {}])
def test_path_argument_requires_bounded_string(value):
    with pytest.raises(ValidationError):
        adapter.ReadTextFileArguments.model_validate({"relative_path": value})


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "task_id", "root_path", "context", "conversation_id"])
def test_model_cannot_supply_identity(field):
    with pytest.raises(ValidationError):
        adapter.ReadTextFileArguments.model_validate({"relative_path": "a", field: "forged"})


def test_registered_adapter_uses_only_trusted_identity(monkeypatch):
    captured = []

    def read(**kwargs):
        captured.append(kwargs)
        return WorkspaceTextFile("notes.txt", "你好\n", 7)

    monkeypatch.setattr(adapter, "read_task_text_file", read)
    tool = TOOL_REGISTRY["read_text_file"]
    assert tool.requires_context
    result = tool.execute(tool.validate_arguments('{"relative_path":"notes.txt"}'), context=CONTEXT)
    assert captured == [{"user_id": 1, "workspace_id": CONTEXT.workspace_id,
                         "task_id": CONTEXT.task_id, "relative_path": "notes.txt"}]
    assert json.loads(result) == {"relative_path": "notes.txt", "content": "你好\n", "byte_count": 7}
    assert "private" not in result


@pytest.mark.parametrize("error,expected", [
    (WorkspaceNotAccessibleError(), "workspace_not_accessible"),
    (WorkspaceDirectoryError("directory_not_found", "PRIVATE"), "workspace_directory_unavailable"),
    (WorkspacePathError("workspace_directory_unbound", "PRIVATE"), "workspace_directory_unbound"),
    (WorkspacePathError("path_outside_workspace", "PRIVATE"), "workspace_path_rejected"),
    *[(WorkspaceFileError(code, "PRIVATE"), code) for code in (
        "file_read_unsupported", "file_not_regular", "file_too_large", "file_not_utf8_text", "file_changed",
    )],
    *[(WorkspaceFileError(code, "PRIVATE"), "file_unavailable") for code in (
        "file_not_found", "file_access_denied", "file_unavailable",
    )],
])
def test_service_errors_are_whitelisted(monkeypatch, error, expected):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(adapter, "read_task_text_file", fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.read_text_file(context=CONTEXT, relative_path="a")
    assert caught.value.code == expected
    assert caught.value.message == SafeToolExecutionError(expected).message
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_unknown_safe_code_rejected():
    with pytest.raises(ValueError):
        SafeToolExecutionError("PRIVATE")


@pytest.mark.parametrize("provided", [None, {}, CONTEXT])
def test_capability_filter_and_schema(provided):
    tools = model_tools_for_context(provided)
    names = {tool["function"]["name"] for tool in tools}
    expected = {"get_current_time", "calculate_rectangle_area"}
    if provided is CONTEXT:
        expected.update({"read_text_file", "list_directory", "search_text_file"})
    assert names == expected
    schema = TOOL_REGISTRY["read_text_file"].as_model_tool()["function"]["parameters"]
    assert set(schema["properties"]) == {"relative_path"}
    assert schema["additionalProperties"] is False
    assert len(TOOLS) == 2


def test_lists_and_nested_schemas_are_independent():
    first = model_tools_for_context(CONTEXT)
    second = model_tools_for_context(CONTEXT)
    first[0]["function"]["parameters"]["properties"].clear()
    first.pop()
    assert second == model_tools_for_context(CONTEXT)
    assert TOOLS == model_tools_for_context()


@pytest.mark.parametrize("provided", [None, CONTEXT])
def test_actual_model_request_filters_tools_without_sending_identity(provided):
    create = AsyncMock(return_value=build_text_response("done"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    maker = DeepSeekDecisionMaker(client=client, model="test", system_prompt="system", user_prompt="hello", tool_context=provided)
    asyncio.run(maker(()))
    payload = create.call_args.kwargs
    assert payload["tools"] == model_tools_for_context(provided)
    assert "private" not in json.dumps(payload)
    assert "tool_context" not in payload


@pytest.mark.parametrize("kind", ["success", "safe", "unknown", "no-context"])
def test_model_runtime_file_tool_round_trip(monkeypatch, kind):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        if kind == "safe":
            raise WorkspaceFileError("file_too_large", "PRIVATE/path")
        if kind == "unknown":
            raise RuntimeError("PRIVATE database connection")
        return WorkspaceTextFile("a", "text", 4)

    monkeypatch.setattr(adapter, "read_task_text_file", read)
    create = AsyncMock(side_effect=[
        build_tool_response(("call-file", "read_text_file", '{"relative_path":"a"}')),
        build_text_response("done"),
    ])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provided = None if kind == "no-context" else CONTEXT
    maker = DeepSeekDecisionMaker(client=client, model="test", system_prompt="system", user_prompt="hello", tool_context=provided)

    async def scenario():
        return [event async for event in runtime.stream_agent_loop(maker, tool_context=provided)]

    events = asyncio.run(scenario())
    observation = events[-1].result.observations[0]
    message = create.call_args.kwargs["messages"][-2]
    assert message["role"] == "tool" and message["tool_call_id"] == "call-file"
    if kind == "success":
        assert isinstance(events[1], runtime.ToolCallSucceeded)
        assert json.loads(observation.result)["content"] == "text"
    else:
        assert isinstance(events[1], runtime.ToolCallFailed)
        assert observation.code == "tool_execution_failed"
        assert observation.details == {"safe": "file_too_large", "unknown": "RuntimeError",
                                       "no-context": "tool_context_required"}[kind]
        assert (observation.duration_ms is None) is (kind == "no-context")
        assert json.loads(message["content"])["type"] == "tool_error"
        assert "PRIVATE" not in message["content"]
    assert len(calls) == (0 if kind == "no-context" else 1)
