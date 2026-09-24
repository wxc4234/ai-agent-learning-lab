"""参数门禁、可信上下文、固定错误、公开协议与编码预算。"""

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.services.workspace.git.status_parser import GitStatusEntry, GitStatusSnapshot
from app.tools import git_sample_status as adapter
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY

CONTEXT = ToolExecutionContext(1, 'c' * 32, 'w' * 32, 't' * 32)


@pytest.fixture
def execution(monkeypatch):
    manager = adapter.TaskGitSamples()
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return GitStatusSnapshot((), 0)
    monkeypatch.setattr(manager, 'read_status', read)
    return adapter.make_git_sample_status_executor(manager), manager, calls


@pytest.mark.parametrize('field', ['relative_path', 'root', 'argv', 'command', 'user_id', 'workspace_id', 'task_id', 'conversation_id', 'manager', 'context', 'max_bytes'])
def test_empty_schema_rejects_extra(field, execution):
    execute, _, calls = execution
    with pytest.raises(ValidationError):
        adapter.GitSampleStatusArguments.model_validate({field: 'PRIVATE'})
    if field != 'context':
        with pytest.raises(SafeToolExecutionError) as caught:
            execute(context=CONTEXT, **{field: 'PRIVATE'})
        assert caught.value.code == 'git_status_request_rejected'
    assert not calls


@pytest.mark.parametrize('context', [None, {}, 'forged'])
def test_context_gate(execution, context):
    execute, _, calls = execution
    with pytest.raises(SafeToolExecutionError) as caught:
        execute(context=context)
    assert caught.value.code == 'workspace_not_accessible' and not calls


def test_schema_source_empty_result_and_identity(execution):
    execute, _, calls = execution
    schema = adapter.GitSampleStatusArguments.model_json_schema()
    assert schema['properties'] == {} and schema['additionalProperties'] is False
    assert 'git_sample_status' not in TOOL_REGISTRY
    result = json.loads(execute(context=CONTEXT))
    assert result == {'source': 'task_git_sample', 'status': 'complete', 'entries': [], 'byte_count': 0,
                      'submodules': 'ignored', 'untracked_files': 'all'}
    assert calls == [{'user_id': 1, 'workspace_id': 'w' * 32, 'task_id': 't' * 32}]
    assert 'clean' not in result


def test_public_entry_protocol(execution, monkeypatch):
    execute, manager, _ = execution
    rows = (GitStatusEntry('RM', 'tracked', '中文\nnew', ' old '),
            GitStatusEntry('UU', 'unmerged', 'conflict'), GitStatusEntry('??', 'untracked', 'new'))
    monkeypatch.setattr(manager, 'read_status', lambda **kwargs: GitStatusSnapshot(rows, 50))
    result = json.loads(execute(context=CONTEXT))
    assert result['entries'][0] == {'xy': 'RM', 'kind': 'tracked', 'path': '中文\nnew',
                                    'original_path': ' old ', 'index_status': 'R', 'worktree_status': 'M'}
    assert result['entries'][1]['index_status'] is None
    assert result['entries'][2]['original_path'] is None
    assert 'root' not in result and 'workspace_id' not in result


@pytest.mark.parametrize('code', sorted(adapter._GIT_ERROR_CODES) + ['PRIVATE'])
def test_capture_error_whitelist(execution, monkeypatch, code):
    execute, manager, calls = execution
    def fail(**kwargs):
        calls.append(kwargs)
        raise adapter.GitStatusCaptureError(code)
    monkeypatch.setattr(manager, 'read_status', fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        execute(context=CONTEXT)
    assert caught.value.code == (code if code in adapter._GIT_ERROR_CODES else 'git_status_unavailable')
    assert 'PRIVATE' not in str(caught.value) and len(calls) == 1


@pytest.mark.parametrize('error,code', [
    (adapter.WorkspaceNotAccessibleError(), 'workspace_not_accessible'),
    (adapter.TaskGitSampleError(), 'task_git_sample_unavailable'),
    (adapter.GitStatusParseError('git_status_invalid_format'), 'git_status_invalid_format'),
    (adapter.GitStatusParseError('git_status_invalid_input'), 'git_status_unavailable'),
    (RuntimeError('PRIVATE'), 'git_status_unavailable'),
])
def test_other_safe_errors(execution, monkeypatch, error, code):
    execute, manager, _ = execution
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(manager, 'read_status', fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        execute(context=CONTEXT)
    assert caught.value.code == code and 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('error', [asyncio.CancelledError(), KeyboardInterrupt()])
def test_cancellation_propagates(execution, monkeypatch, error):
    execute, manager, _ = execution
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(manager, 'read_status', fail)
    with pytest.raises(type(error)) as caught:
        execute(context=CONTEXT)
    assert caught.value is error


@pytest.mark.parametrize('snapshot', [object(), GitStatusSnapshot((object(),), 0),
    GitStatusSnapshot((GitStatusEntry('??', 'untracked', '\ud800'),), 1)])
def test_projection_failure_never_empty_success(execution, monkeypatch, snapshot):
    execute, manager, _ = execution
    monkeypatch.setattr(manager, 'read_status', lambda **kwargs: snapshot)
    with pytest.raises(SafeToolExecutionError) as caught:
        execute(context=CONTEXT)
    assert caught.value.code == 'git_status_unavailable'


def test_json_expansion_budget(execution, monkeypatch):
    execute, manager, _ = execution
    # 原始路径字节约250KiB，控制字符转义后超过1MiB，不能只限制源输出。
    rows = tuple(GitStatusEntry('??', 'untracked', '\x01' * 4000) for _ in range(64))
    monkeypatch.setattr(manager, 'read_status', lambda **kwargs: GitStatusSnapshot(rows, 256256))
    with pytest.raises(SafeToolExecutionError) as caught:
        execute(context=CONTEXT)
    assert caught.value.code == 'git_status_result_too_large'


def test_manager_cannot_be_model_mapping():
    with pytest.raises(TypeError):
        adapter.make_git_sample_status_executor({})
