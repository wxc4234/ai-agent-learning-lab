"""补丁适配：输入边界、可信上下文、固定错误与显式投影。"""

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.edits.workspace_edit_preview import EditPreviewError, TextEditPreview
from app.services.workspace.edits.workspace_file_preview import WorkspaceFileEditPreview
from app.services.workspace.edits.workspace_unified_patch import UnifiedPatchError
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.tools import preview_file_patch as adapter
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, tools_for_execution
from tests.tools.test_read_file_tool import CONTEXT


ARGS = {'relative_path': 'file.txt', 'patch': '--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n'}


@pytest.mark.parametrize('field', ARGS)
@pytest.mark.parametrize('value', [None, 1, True, [], {}, '', 'a\x00b', '\ud800', 'a\r\nb'])
def test_invalid_fields_rejected_before_service(monkeypatch, field, value):
    monkeypatch.setattr(adapter, 'preview_task_file_patch', lambda **kwargs: pytest.fail('must not read'))
    with pytest.raises(ValidationError):
        adapter.PreviewFilePatchArguments.model_validate(ARGS | {field: value})
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.preview_file_patch(context=CONTEXT, **(ARGS | {field: value}))
    assert caught.value.code == 'patch_request_rejected'


@pytest.mark.parametrize('field', ARGS)
def test_required_fields(field):
    values = dict(ARGS)
    del values[field]
    with pytest.raises(ValidationError):
        adapter.PreviewFilePatchArguments.model_validate(values)


@pytest.mark.parametrize('field', ['context', 'user_id', 'workspace_id', 'task_id', 'conversation_id', 'root_path', 'approved', 'baseline_sha256', 'max_bytes'])
def test_extra_fields_rejected(field):
    with pytest.raises(ValidationError):
        adapter.PreviewFilePatchArguments.model_validate(ARGS | {field: 'forged'})


@pytest.mark.parametrize('field,limit', [('relative_path', adapter.MAX_PATH_BYTES), ('patch', adapter.MAX_PATCH_BYTES)])
def test_utf8_and_character_boundaries(field, limit):
    exact = '😀' * (limit // 4)
    assert adapter.PreviewFilePatchArguments.model_validate(ARGS | {field: exact}).model_dump()[field] == exact
    for value in (exact + 'x', 'x' * (limit + 1)):
        with pytest.raises(ValidationError):
            adapter.PreviewFilePatchArguments.model_validate(ARGS | {field: value})


def test_schema_and_contextual_registration():
    schema = adapter.PreviewFilePatchArguments.model_json_schema()
    assert set(schema['properties']) == set(schema['required']) == set(ARGS)
    assert schema['additionalProperties'] is False
    assert TOOL_REGISTRY['preview_file_patch'].arguments_model is adapter.PreviewFilePatchArguments
    assert 'preview_file_patch' in {tool.name for tool in tools_for_execution(context=CONTEXT)}
    assert 'preview_file_patch' not in {tool.name for tool in tools_for_execution(context=None)}
    value = ' \n\t'
    assert adapter.PreviewFilePatchArguments(**(ARGS | {'patch': value})).patch == value


@pytest.mark.parametrize('context', [None, {}, 'forged'])
def test_context_type_gate(monkeypatch, context):
    monkeypatch.setattr(adapter, 'preview_task_file_patch', lambda **kwargs: pytest.fail('must not read'))
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.preview_file_patch(context=context, **ARGS)
    assert caught.value.code == 'workspace_not_accessible'


@pytest.mark.parametrize('truncated', [True, False])
def test_projection_and_server_identity(monkeypatch, truncated):
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return WorkspaceFileEditPreview('file.txt', 'a' * 64, TextEditPreview('PRIVATE_FULL_CANDIDATE', 4, 5, '审阅片段', truncated))
    monkeypatch.setattr(adapter, 'preview_task_file_patch', generate)
    raw = adapter.preview_file_patch(context=CONTEXT, **ARGS)
    assert json.loads(raw) == {
        'status': 'preview_only', 'relative_path': 'file.txt', 'baseline_sha256': 'a' * 64,
        'before_byte_count': 4, 'after_byte_count': 5, 'diff': '审阅片段', 'diff_truncated': truncated,
    }
    assert 'PRIVATE_FULL_CANDIDATE' not in raw and 'updated_content' not in raw
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
    (RuntimeError('PRIVATE'), 'patch_preview_unavailable'),
])
def test_fixed_safe_error_mapping(monkeypatch, error, code):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(adapter, 'preview_task_file_patch', fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.preview_file_patch(context=CONTEXT, **ARGS)
    assert caught.value.code == code
    assert caught.value.message == SafeToolExecutionError(code).message
    assert 'PRIVATE' not in str(caught.value)
    assert set(vars(caught.value)) == {'code', 'message'}


def test_cancellation_propagates_unchanged(monkeypatch):
    error = asyncio.CancelledError()
    def cancel(**kwargs):
        raise error
    monkeypatch.setattr(adapter, 'preview_task_file_patch', cancel)
    with pytest.raises(asyncio.CancelledError) as caught:
        adapter.preview_file_patch(context=CONTEXT, **ARGS)
    assert caught.value is error


def test_projection_failure_is_safe(monkeypatch):
    monkeypatch.setattr(adapter, 'preview_task_file_patch', lambda **kwargs: object())
    with pytest.raises(SafeToolExecutionError) as caught:
        adapter.preview_file_patch(context=CONTEXT, **ARGS)
    assert caught.value.code == 'patch_preview_unavailable'


@pytest.mark.parametrize('size,truncated', [(3, False), (20000, True)])
def test_real_patch_review_chain_with_controlled_authorized_read(monkeypatch, size, truncated):
    from hashlib import sha256
    from app.services.workspace.edits import workspace_patch_preview as preview_service
    from app.services.workspace.files.workspace_file import WorkspaceTextFile

    before, after = 'a' * size + '\n', 'b' * size + '\n'
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return WorkspaceTextFile('file.txt', before, len(before.encode()))
    # 仅替换授权读取出口；补丁解析、候选、审阅构造和工具投影均真实执行。
    monkeypatch.setattr(preview_service, 'read_task_text_file', read)
    patch = f'--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-{before}+{after}'
    result = json.loads(adapter.preview_file_patch(context=CONTEXT, relative_path='file.txt', patch=patch))
    assert len(calls) == 1
    assert result['baseline_sha256'] == sha256(before.encode()).hexdigest()
    assert result['before_byte_count'] == len(before) and result['after_byte_count'] == len(after)
    assert result['diff'].startswith('--- before\n+++ after\n')
    assert len(result['diff']) <= 16384
    assert result['diff_truncated'] is truncated
    assert 'updated_content' not in result and result['status'] == 'preview_only'
