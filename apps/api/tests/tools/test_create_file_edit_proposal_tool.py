"""提案创建工具的参数、提交回执、安全错误与模型往返。"""

import asyncio
import json
from types import SimpleNamespace
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError, OperationalError

import pytest
from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import ToolErrorObservation, ToolObservation, run_agent_loop
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.edits.workspace_edit_preview import EditPreviewError, MAX_EDIT_TEXT_BYTES
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.services.workspace.proposals.file_edit_proposal_service import ProposalBindingChangedError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.tools import create_file_edit_proposal as adapter
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError, model_tools_for_context, tools_for_execution
from tests.model.test_model_decision import build_text_response, build_tool_response
from tests.tools.test_read_file_tool import CONTEXT


ARGS = {"relative_path": "src/file.txt", "old_text": "old", "new_text": "new"}
TOOL = TOOL_REGISTRY["create_file_edit_proposal"]


def result(truncated=False):
    # 附加私有哨兵，确保工具显式投影而不序列化整个服务对象。
    return SimpleNamespace(
        proposal_id="p" * 32, status="pending", relative_path="src/file.txt",
        baseline_sha256="a" * 64, proposed_sha256="b" * 64,
        diff_truncated=truncated, created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        bound_root="PRIVATE", proposed_content="INTERNAL_FULL_CONTENT",
    )


@pytest.mark.parametrize("field", list(ARGS))
def test_fields_required(field):
    args = dict(ARGS)
    del args[field]
    with pytest.raises(ValidationError):
        TOOL.validate_arguments(json.dumps(args))


@pytest.mark.parametrize("field", list(ARGS))
@pytest.mark.parametrize("value", [None, 1, True, [], {}])
def test_strict_types(field, value):
    with pytest.raises(ValidationError):
        TOOL.validate_arguments(json.dumps(ARGS | {field: value}))


@pytest.mark.parametrize("field,value", [
    ("relative_path", ""), ("relative_path", "x" * 4097), ("old_text", ""),
    *[(field, value) for field in ("old_text", "new_text") for value in (
        "a\x00b", chr(0xD800), "x" * (MAX_EDIT_TEXT_BYTES + 1), "😀" * (MAX_EDIT_TEXT_BYTES // 4 + 1),
    )],
])
def test_text_boundaries(field, value):
    with pytest.raises(ValidationError):
        adapter.CreateFileEditProposalArguments.model_validate(ARGS | {field: value})


@pytest.mark.parametrize("field", ["context", "user_id", "workspace_id", "task_id", "root_path", "approved", "baseline_sha256", "max_bytes", "proposal_id", "status", "proposed_sha256"])
def test_extra_fields_rejected(field):
    with pytest.raises(ValidationError):
        TOOL.validate_arguments(json.dumps(ARGS | {field: True}))


def test_schema_deletion_exact_bytes_and_whitespace():
    schema = TOOL.as_model_tool()["function"]["parameters"]
    assert set(schema["properties"]) == set(schema["required"]) == set(ARGS)
    assert schema["additionalProperties"] is False
    assert adapter.CreateFileEditProposalArguments(**(ARGS | {"new_text": ""})).new_text == ""
    exact = "😀" * (MAX_EDIT_TEXT_BYTES // 4)
    assert adapter.CreateFileEditProposalArguments(**(ARGS | {"new_text": exact})).new_text == exact
    text = " \r\n\t"
    assert adapter.CreateFileEditProposalArguments(**(ARGS | {"old_text": text})).old_text == text


@pytest.mark.parametrize("truncated", [False, True])
def test_public_fields_and_context(monkeypatch, truncated):
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return result(truncated)

    monkeypatch.setattr(adapter, "create_task_file_edit_proposal", generate)
    raw = TOOL.execute(TOOL.validate_arguments(json.dumps(ARGS)), context=CONTEXT)
    assert json.loads(raw) == {
        "proposal_id": "p" * 32, "status": "pending", "relative_path": "src/file.txt",
        "baseline_sha256": "a" * 64, "proposed_sha256": "b" * 64,
        "diff_truncated": truncated, "created_at": "2026-09-21T00:00:00+00:00",
    }
    assert "PRIVATE" not in raw
    assert "INTERNAL_FULL_CONTENT" not in raw and "updated_content" not in raw
    assert calls == [ARGS | {"user_id": CONTEXT.user_id, "workspace_id": CONTEXT.workspace_id, "task_id": CONTEXT.task_id}]


@pytest.mark.parametrize("error,code", [
    (WorkspaceNotAccessibleError(), "workspace_not_accessible"),
    (ProposalBindingChangedError(), "proposal_binding_changed"),
    (SQLAlchemyError("PRIVATE"), "proposal_save_unconfirmed"),
    (OperationalError("PRIVATE SQL", {}, Exception("PRIVATE")), "proposal_save_unconfirmed"),
    (WorkspaceDirectoryError("directory_not_found", "PRIVATE"), "workspace_directory_unavailable"),
    (WorkspacePathError("workspace_directory_unbound", "PRIVATE"), "workspace_directory_unbound"),
    (WorkspacePathError("path_outside_workspace", "PRIVATE"), "workspace_path_rejected"),
    *[(WorkspaceFileError(code, "PRIVATE"), code) for code in (
        "file_read_unsupported", "file_not_regular", "file_too_large", "file_not_utf8_text", "file_changed",
    )],
    (WorkspaceFileError("future_code", "PRIVATE"), "file_unavailable"),
    *[(EditPreviewError(code, "PRIVATE"), code) for code in (
        "invalid_edit_text", "edit_text_too_large", "empty_old_text", "edit_no_change",
        "edit_target_not_found", "edit_target_ambiguous", "edit_preview_too_many_lines",
    )],
    (EditPreviewError("future_code", "PRIVATE"), "edit_preview_unavailable"),
])
def test_safe_error_mapping(monkeypatch, error, code):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(adapter, "create_task_file_edit_proposal", fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.create_file_edit_proposal(context=CONTEXT, **ARGS)
    assert caught.value.code == code
    assert str(caught.value) == SafeToolExecutionError(code).message
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("context", [None, {}, "forged"])
def test_context_gate_and_visibility(monkeypatch, context):
    monkeypatch.setattr(adapter, "create_task_file_edit_proposal", lambda **kwargs: pytest.fail("must not execute"))
    with pytest.raises(ToolContextRequiredError):
        TOOL.execute(TOOL.validate_arguments(json.dumps(ARGS)), context=context)
    assert TOOL.name not in {tool.name for tool in tools_for_execution(context=context)}
    assert TOOL.name not in {tool["function"]["name"] for tool in model_tools_for_context(context)}
    assert TOOL in tools_for_execution(context=CONTEXT)


@pytest.mark.parametrize("error", [RuntimeError("PRIVATE"), asyncio.CancelledError()])
def test_unknown_and_cancel_propagate(monkeypatch, error):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(adapter, "create_task_file_edit_proposal", fail)
    with pytest.raises(type(error)) as caught:
        adapter.create_file_edit_proposal(context=CONTEXT, **ARGS)
    assert caught.value is error


@pytest.mark.parametrize("kind", ["success", "truncated", "safe", "database", "unknown", "invalid", "no-context"])
def test_model_proposal_roundtrip(monkeypatch, kind):
    calls = []
    context = None if kind == "no-context" else CONTEXT
    definitions = tools_for_execution(context=context)

    def generate(**kwargs):
        calls.append(kwargs)
        if kind == "safe":
            raise EditPreviewError("edit_target_ambiguous", "PRIVATE")
        if kind == "database":
            raise SQLAlchemyError("PRIVATE")
        if kind == "unknown":
            raise RuntimeError("PRIVATE")
        return result(kind == "truncated")

    async def create(**kwargs):
        assert kwargs["tools"] == [tool.as_model_tool() for tool in definitions]
        messages = [message for message in kwargs["messages"] if message["role"] == "tool"]
        if not messages:
            args = ARGS | ({"approved": True} if kind == "invalid" else {})
            return build_tool_response(("preview", "create_file_edit_proposal", json.dumps(args)))
        raw = messages[-1]["content"]
        assert "PRIVATE" not in raw and "INTERNAL_FULL_CONTENT" not in raw
        if kind in {"success", "truncated"}:
            payload = json.loads(raw)
            assert payload["status"] == "pending"
            assert payload["diff_truncated"] is (kind == "truncated")
        return build_text_response("工具结果已接收，文件尚未写入")

    monkeypatch.setattr(adapter, "create_task_file_edit_proposal", generate)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    maker = DeepSeekDecisionMaker(client=client, model="test", system_prompt="system", user_prompt="preview",
                                 tool_context=context, tool_definitions=definitions)
    outcome = asyncio.run(run_agent_loop(maker, tool_context=context, tool_definitions=definitions))
    assert outcome.answer == "工具结果已接收，文件尚未写入"
    assert len(calls) == (0 if kind in {"invalid", "no-context"} else 1)
    observation = outcome.observations[0]
    if kind in {"success", "truncated"}:
        assert isinstance(observation, ToolObservation)
    else:
        assert isinstance(observation, ToolErrorObservation)
        if kind in {"invalid", "no-context"}:
            assert observation.code == {"invalid": "invalid_tool_arguments", "no-context": "unknown_tool"}[kind]
        else:
            assert observation.details == {"safe": "edit_target_ambiguous", "database": "proposal_save_unconfirmed", "unknown": "RuntimeError"}[kind]
