"""目录工具参数、错误契约及列目录后选择文件的模型链路。"""

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
from app.services.workspace.workspace_listing import WorkspaceDirectoryEntry, WorkspaceDirectoryListing, WorkspaceListingError
from app.services.workspace.workspace_path import WorkspacePathError
from app.tools import list_directory as adapter, read_file
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, model_tools_for_context
from tests.model.test_model_decision import build_text_response, build_tool_response
from tests.tools.test_read_file_tool import CONTEXT


@pytest.mark.parametrize("value", [None, True, 12, "", "x" * 4097, [], {}])
def test_strict_path(value):
    with pytest.raises(ValidationError):
        adapter.ListDirectoryArguments.model_validate({"relative_path": value})


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "task_id", "context", "root_path", "recursive", "limit"])
def test_extra_capabilities_rejected(field):
    with pytest.raises(ValidationError):
        adapter.ListDirectoryArguments.model_validate({field: 1})


@pytest.mark.parametrize("truncated", [False, True])
def test_defaults_identity_and_result(monkeypatch, truncated):
    captured = []

    def listing(**kwargs):
        captured.append(kwargs)
        return WorkspaceDirectoryListing(".", (WorkspaceDirectoryEntry("中文", "symlink"),), truncated)

    monkeypatch.setattr(adapter, "list_task_directory", listing)
    tool = TOOL_REGISTRY["list_directory"]
    assert tool.requires_context
    result = json.loads(tool.execute(tool.validate_arguments("{}"), context=CONTEXT))
    assert captured == [{"user_id": CONTEXT.user_id, "workspace_id": CONTEXT.workspace_id,
                         "task_id": CONTEXT.task_id, "relative_path": "."}]
    assert result == {"relative_path": ".", "entries": [{"name": "中文", "kind": "symlink"}], "truncated": truncated}
    schema = tool.as_model_tool()["function"]["parameters"]
    assert set(schema["properties"]) == {"relative_path"}
    assert schema["properties"]["relative_path"]["default"] == "."
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("error,expected", [
    (WorkspaceNotAccessibleError(), "workspace_not_accessible"),
    (WorkspaceDirectoryError("directory_not_found", "PRIVATE"), "workspace_directory_unavailable"),
    (WorkspacePathError("workspace_directory_unbound", "PRIVATE"), "workspace_directory_unbound"),
    (WorkspacePathError("path_outside_workspace", "PRIVATE"), "workspace_path_rejected"),
    *[(WorkspaceListingError(code, "PRIVATE"), code) for code in (
        "directory_listing_unsupported", "directory_listing_not_found", "directory_listing_not_directory",
        "directory_listing_access_denied", "directory_listing_changed", "directory_listing_unavailable",
    )],
    (WorkspaceListingError("future-code", "PRIVATE"), "directory_listing_unavailable"),
])
def test_safe_failure_mapping(monkeypatch, error, expected):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(adapter, "list_task_directory", fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.list_directory(context=CONTEXT)
    assert caught.value.code == expected
    assert str(caught.value) == SafeToolExecutionError(expected).message
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("kind", ["success", "safe", "unknown", "no-context"])
def test_model_discovers_then_reads_or_observes_failure(monkeypatch, kind):
    calls = []
    requests = []

    def listing(**kwargs):
        calls.append("list")
        if kind == "safe":
            raise WorkspaceListingError("directory_listing_changed", "PRIVATE path")
        if kind == "unknown":
            raise RuntimeError("PRIVATE database")
        return WorkspaceDirectoryListing(".", (WorkspaceDirectoryEntry("discovered.txt", "file"),), False)

    def read(**kwargs):
        calls.append("read")
        assert kwargs["relative_path"] == "discovered.txt"
        assert kwargs["task_id"] == CONTEXT.task_id
        return WorkspaceTextFile("discovered.txt", "content", 7)

    async def create(**kwargs):
        requests.append(kwargs["tools"])
        messages = [message for message in kwargs["messages"] if message["role"] == "tool"]
        if not messages:
            return build_tool_response(("list-call", "list_directory", "{}"))
        observed = json.loads(messages[-1]["content"])
        if len(messages) == 1 and kind == "success":
            # 第二次动作由实际目录Observation选取文件名，不硬编码响应顺序。
            assert observed["truncated"] is False
            name = observed["entries"][0]["name"]
            return build_tool_response(("read-call", "read_text_file", json.dumps({"relative_path": name})))
        assert "PRIVATE" not in messages[-1]["content"]
        return build_text_response("done")

    monkeypatch.setattr(adapter, "list_task_directory", listing)
    monkeypatch.setattr(read_file, "read_task_text_file", read)
    provided = None if kind == "no-context" else CONTEXT
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    maker = DeepSeekDecisionMaker(client=client, model="test", system_prompt="system", user_prompt="inspect", tool_context=provided)
    result = asyncio.run(run_agent_loop(maker, tool_context=provided))
    assert all(tools == model_tools_for_context(provided) for tools in requests)
    if kind == "success":
        assert calls == ["list", "read"]
        assert all(isinstance(observation, ToolObservation) for observation in result.observations)
        assert result.steps_taken == 3
    else:
        assert calls == ([] if kind == "no-context" else ["list"])
        observation = result.observations[0]
        assert isinstance(observation, ToolErrorObservation)
        assert observation.details == {"safe": "directory_listing_changed", "unknown": "RuntimeError", "no-context": "tool_context_required"}[kind]
    assert result.status == "completed"
