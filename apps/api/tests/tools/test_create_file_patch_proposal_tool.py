"""补丁提案工具的参数、上下文、错误与公开回执。"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

import pytest
from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.edits.workspace_edit_preview import EditPreviewError
from app.services.workspace.edits.workspace_unified_patch import UnifiedPatchError
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.tools import create_file_patch_proposal as adapter
from app.services.workspace.edits.workspace_unified_patch import MAX_PATCH_BYTES, MAX_PATH_BYTES
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, tools_for_execution
from tests.tools.test_read_file_tool import CONTEXT


ARGS = {'relative_path': 'file.txt', 'patch': '--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n'}


@pytest.mark.parametrize('field', ARGS)
@pytest.mark.parametrize('value', [None, 1, True, [], {}, '', 'a\x00b', '\ud800', 'a\r\nb'])
def test_invalid_fields_rejected_before_service(monkeypatch, field, value):
    monkeypatch.setattr(adapter, 'create_task_file_patch_proposal', lambda **kwargs: pytest.fail('must not read'))
    with pytest.raises(ValidationError):
        adapter.CreateFilePatchProposalArguments.model_validate(ARGS | {field: value})
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.create_file_patch_proposal(context=CONTEXT, **(ARGS | {field: value}))
    assert caught.value.code == 'patch_request_rejected'


@pytest.mark.parametrize('field', ARGS)
def test_required_fields(field):
    values = dict(ARGS)
    del values[field]
    with pytest.raises(ValidationError):
        adapter.CreateFilePatchProposalArguments.model_validate(values)


@pytest.mark.parametrize('field', ['context', 'user_id', 'workspace_id', 'task_id', 'conversation_id', 'root_path', 'approved', 'baseline_sha256', 'max_bytes', 'proposal_id', 'status', 'proposed_content'])
def test_extra_fields_rejected(field):
    with pytest.raises(ValidationError):
        adapter.CreateFilePatchProposalArguments.model_validate(ARGS | {field: 'forged'})


@pytest.mark.parametrize('field,limit', [('relative_path', MAX_PATH_BYTES), ('patch', MAX_PATCH_BYTES)])
def test_utf8_and_character_boundaries(field, limit):
    exact = '😀' * (limit // 4)
    assert adapter.CreateFilePatchProposalArguments.model_validate(ARGS | {field: exact}).model_dump()[field] == exact
    for value in (exact + 'x', 'x' * (limit + 1)):
        with pytest.raises(ValidationError):
            adapter.CreateFilePatchProposalArguments.model_validate(ARGS | {field: value})


def test_schema_and_contextual_registration():
    schema = adapter.CreateFilePatchProposalArguments.model_json_schema()
    assert set(schema['properties']) == set(schema['required']) == set(ARGS)
    assert schema['additionalProperties'] is False
    assert TOOL_REGISTRY['create_file_patch_proposal'].arguments_model is adapter.CreateFilePatchProposalArguments
    assert 'create_file_patch_proposal' in {tool.name for tool in tools_for_execution(context=CONTEXT)}
    assert 'create_file_patch_proposal' not in {tool.name for tool in tools_for_execution(context=None)}


@pytest.mark.parametrize('context', [None, {}, 'forged'])
def test_context_type_gate(monkeypatch, context):
    monkeypatch.setattr(adapter, 'create_task_file_patch_proposal', lambda **kwargs: pytest.fail('must not read'))
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.create_file_patch_proposal(context=context, **ARGS)
    assert caught.value.code == 'workspace_not_accessible'


@pytest.mark.parametrize('truncated', [True, False])
def test_projection_and_server_identity(monkeypatch, truncated):
    calls = []
    receipt = {
        'proposal_id': 'p' * 32, 'status': 'pending', 'relative_path': 'file.txt',
        'baseline_sha256': 'a' * 64, 'proposed_sha256': 'b' * 64,
        'diff_truncated': truncated, 'created_at': datetime(2026, 9, 24, tzinfo=timezone.utc),
    }
    def save(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(**receipt, proposed_content='PRIVATE', bound_root='/PRIVATE')
    monkeypatch.setattr(adapter, 'create_task_file_patch_proposal', save)
    raw = adapter.create_file_patch_proposal(context=CONTEXT, **ARGS)
    assert json.loads(raw) == receipt | {'created_at': receipt['created_at'].isoformat()}
    assert 'PRIVATE' not in raw
    assert calls == [ARGS | {'user_id': CONTEXT.user_id, 'workspace_id': CONTEXT.workspace_id, 'task_id': CONTEXT.task_id}]


@pytest.mark.parametrize('error,code', [
    (WorkspaceNotAccessibleError(), 'workspace_not_accessible'),
    (WorkspaceDirectoryError('unknown', 'PRIVATE'), 'workspace_directory_unavailable'),
    (WorkspacePathError('workspace_directory_unbound', 'PRIVATE'), 'workspace_directory_unbound'),
    (WorkspacePathError('unknown', 'PRIVATE'), 'workspace_path_rejected'),
    *[(WorkspaceFileError(code, 'PRIVATE'), code) for code in ('file_read_unsupported', 'file_not_regular', 'file_too_large', 'file_not_utf8_text', 'file_changed')],
    (WorkspaceFileError('unknown', 'PRIVATE'), 'file_unavailable'),
    *[(UnifiedPatchError(code), code) for code in sorted(adapter.PATCH_ERROR_CODES)],
    (UnifiedPatchError('PRIVATE'), 'patch_preview_unavailable'),
    *[(EditPreviewError(code, 'PRIVATE'), code) for code in ('invalid_edit_text', 'edit_text_too_large', 'edit_preview_too_many_lines')],
    (EditPreviewError('PRIVATE', 'PRIVATE'), 'patch_preview_unavailable'),
    (RuntimeError('PRIVATE'), 'proposal_save_unconfirmed'),
    (SQLAlchemyError('PRIVATE'), 'proposal_save_unconfirmed'),
    (adapter.ProposalBindingChangedError(), 'proposal_binding_changed'),
])
def test_fixed_safe_error_mapping(monkeypatch, error, code):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(adapter, 'create_task_file_patch_proposal', fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.create_file_patch_proposal(context=CONTEXT, **ARGS)
    assert caught.value.code == code
    assert caught.value.message == SafeToolExecutionError(code).message
    assert 'PRIVATE' not in str(caught.value)
    assert set(vars(caught.value)) == {'code', 'message'}


def test_cancellation_propagates_unchanged(monkeypatch):
    error = asyncio.CancelledError()
    def cancel(**kwargs):
        raise error
    monkeypatch.setattr(adapter, 'create_task_file_patch_proposal', cancel)
    with pytest.raises(asyncio.CancelledError) as caught:
        adapter.create_file_patch_proposal(context=CONTEXT, **ARGS)
    assert caught.value is error


@pytest.mark.parametrize('result', [object(), SimpleNamespace(status='approved'), SimpleNamespace(status='pending')])
def test_projection_failure_is_safe(monkeypatch, result):
    monkeypatch.setattr(adapter, 'create_task_file_patch_proposal', lambda **kwargs: result)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.create_file_patch_proposal(context=CONTEXT, **ARGS)
    assert caught.value.code == 'proposal_save_unconfirmed'
