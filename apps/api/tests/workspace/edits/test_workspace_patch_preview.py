"""补丁组合服务：一次授权读取、同源摘要、真实文件只读和异常边界。"""

import asyncio
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.edits import workspace_patch_preview as service
from app.services.workspace.edits.workspace_unified_patch import UnifiedPatchError
from app.services.workspace.files.workspace_file import WorkspaceFileError, WorkspaceTextFile
from tests.workspace.directory import test_workspace_path as path_tests


root = path_tests.root
target = path_tests.target
database = path_tests.database
MARKER = '\\ No newline at end of file\n'


def patch_for(before='old\n', after='new\n', path='src/file.txt'):
    # 本组测试只需单行替换；协议行补 LF 后用标记保留文件 EOF。
    def line(prefix, text):
        return prefix + text if text.endswith('\n') else prefix + text + '\n' + MARKER
    return f'--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n' + line('-', before) + line('+', after)


def preview(**overrides):
    return service.preview_task_file_patch(**{
        'user_id': 1, 'workspace_id': 'w', 'task_id': 't',
        'relative_path': './src//file.txt', 'patch': patch_for(), **overrides,
    })


@pytest.mark.parametrize('before,after', [
    ('old\n', 'new\n'), ('old', 'new'), ('old\n', 'new'),
    ('old', 'new\n'), ('\ufeff中文😀\n', '\ufeff新内容\n'),
])
def test_single_read_canonical_path_and_exact_baseline(monkeypatch, before, after):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        assert len(calls) == 1
        return WorkspaceTextFile('src/file.txt', before, len(before.encode()))

    monkeypatch.setattr(service, 'read_task_text_file', read)
    result = preview(patch=patch_for(before, after))
    assert calls == [{'user_id': 1, 'workspace_id': 'w', 'task_id': 't', 'relative_path': './src//file.txt'}]
    assert result.relative_path == 'src/file.txt'
    assert result.baseline_sha256 == sha256(before.encode()).hexdigest()
    assert result.baseline_sha256 != sha256(after.encode()).hexdigest()
    assert result.preview.updated_content == after
    assert result.preview.before_byte_count == len(before.encode())
    assert result.preview.after_byte_count == len(after.encode())
    assert result.preview.diff.startswith('--- before\n+++ after\n')
    assert not result.preview.diff_truncated
    with pytest.raises(FrozenInstanceError):
        result.baseline_sha256 = 'changed'
    with pytest.raises(FrozenInstanceError):
        result.preview.updated_content = 'changed'


@pytest.mark.parametrize('error', [
    WorkspaceNotAccessibleError(), WorkspaceDirectoryError('directory_not_found', 'safe'),
    WorkspacePathError('workspace_directory_unbound', 'safe'),
    WorkspaceFileError('file_changed', 'safe'), RuntimeError('internal'), asyncio.CancelledError(),
])
def test_read_failure_prevents_parsing(monkeypatch, error):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(service, 'read_task_text_file', fail)
    monkeypatch.setattr(service, 'preview_unified_patch', lambda **kwargs: pytest.fail('must not parse'))
    with pytest.raises(type(error)) as caught:
        preview(patch='malformed')
    assert caught.value is error


@pytest.mark.parametrize('content,patch,code', [
    ('old\n', patch_for(path='other.txt'), 'patch_target_mismatch'),
    ('old\n', patch_for(path='./src//file.txt'), 'patch_target_mismatch'),
    ('OLD\n', patch_for(), 'patch_context_mismatch'),
    ('old\r\n', patch_for(), 'patch_invalid_text'),
    ('old\n', 'invalid', 'patch_invalid_format'),
    ('old\n', patch_for(after='x' * (256 * 1024 + 1)), 'patch_limit_exceeded'),
], ids=['wrong-target', 'noncanonical-header', 'conflict', 'crlf', 'malformed', 'candidate-too-large'])
def test_patch_failure_has_no_review_or_digest(monkeypatch, content, patch, code):
    monkeypatch.setattr(service, 'read_task_text_file', lambda **kwargs: WorkspaceTextFile('src/file.txt', content, len(content)))
    monkeypatch.setattr(service, '_build_review_diff', lambda **kwargs: pytest.fail('must not review'))
    monkeypatch.setattr(service, 'sha256', lambda *args: pytest.fail('must not produce baseline'))
    with pytest.raises(UnifiedPatchError) as caught:
        preview(patch=patch)
    assert caught.value.code == code


def test_review_failure_does_not_return_success(monkeypatch):
    error = RuntimeError('review failed')
    monkeypatch.setattr(service, 'read_task_text_file', lambda **kwargs: WorkspaceTextFile('src/file.txt', 'old\n', 4))
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(service, '_build_review_diff', fail)
    monkeypatch.setattr(service, 'sha256', lambda *args: pytest.fail('must not produce baseline'))
    with pytest.raises(RuntimeError) as caught:
        preview()
    assert caught.value is error


def test_truncation_preserves_complete_candidate(monkeypatch):
    before, after = 'a' * 20000 + '\n', 'b' * 20000 + '\n'
    monkeypatch.setattr(service, 'read_task_text_file', lambda **kwargs: WorkspaceTextFile('src/file.txt', before, len(before)))
    result = preview(patch=patch_for(before, after))
    assert result.preview.diff_truncated
    assert len(result.preview.diff) == 16384
    assert result.preview.updated_content == after
    assert result.baseline_sha256 == sha256(before.encode()).hexdigest()


@pytest.mark.parametrize('before,after', [(b'old\n', b'new\n'), (b'old', b'new'), ('\ufeff中文😀\n'.encode(), '\ufeff更新\n'.encode())])
def test_real_authorization_read_only_and_closed_transaction(database, target, root, monkeypatch, before, after):
    path = root / 'src/file.txt'
    path.write_bytes(before)
    original_mode = path.stat().st_mode
    original = service.preview_unified_patch

    def parse(**kwargs):
        assert database[0] and all(session.closed for session in database[0])
        assert all(not session.in_transaction() for session in database[0])
        return original(**kwargs)

    monkeypatch.setattr(service, 'preview_unified_patch', parse)
    args = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    result = preview(**args, patch=patch_for(before.decode(), after.decode()))
    assert result.preview.updated_content.encode() == after
    assert result.baseline_sha256 == sha256(before).hexdigest()
    assert path.read_bytes() == before and path.stat().st_mode == original_mode
    assert database[1] and all(sql.lstrip().upper().startswith('SELECT') for sql in database[1])


def test_external_change_after_read_cannot_replace_baseline(database, target, root, monkeypatch):
    path = root / 'src/file.txt'
    path.write_bytes(b'old\n')
    read = service.read_task_text_file
    calls = []

    def read_once(**kwargs):
        calls.append(kwargs)
        source = read(**kwargs)
        path.write_bytes(b'external change')
        return source

    monkeypatch.setattr(service, 'read_task_text_file', read_once)
    args = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    result = preview(**args)
    assert len(calls) == 1
    assert result.preview.updated_content == 'new\n'
    assert result.baseline_sha256 == sha256(b'old\n').hexdigest()
    assert path.read_bytes() == b'external change'


@pytest.mark.parametrize('kind', ['foreign', 'wrong_task', 'unbound', 'outside', 'missing', 'invalid_utf8', 'oversized', 'conflict', 'crlf', 'wrong_target'])
def test_real_rejections_preserve_source(engine, database, target, root, monkeypatch, kind):
    path = root / 'src/file.txt'
    content = b'\xff' if kind == 'invalid_utf8' else b'x' * (256 * 1024 + 1) if kind == 'oversized' else b'old\r\n' if kind == 'crlf' else b'old\n'
    path.write_bytes(content)
    args = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    relative = 'src/file.txt'
    patch = patch_for()
    expected = WorkspacePathError
    if kind == 'foreign':
        args['user_id'] = target['other_id']
        expected = WorkspaceNotAccessibleError
    elif kind == 'wrong_task':
        args['task_id'] = 'nonexistent-task'
        expected = WorkspaceNotAccessibleError
    elif kind == 'unbound':
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = None
    elif kind == 'outside':
        (root / 'link').symlink_to(root.parent, target_is_directory=True)
        relative = 'link'
    elif kind == 'missing':
        relative = 'missing'
    elif kind in ('invalid_utf8', 'oversized'):
        expected = WorkspaceFileError
    else:
        expected = UnifiedPatchError
        if kind == 'conflict':
            patch = patch_for(before='wrong\n')
        elif kind == 'wrong_target':
            patch = patch_for(path='other.txt')
    if expected is not UnifiedPatchError:
        monkeypatch.setattr(service, 'preview_unified_patch', lambda **kwargs: pytest.fail('read must fail before parse'))
    with pytest.raises(expected):
        preview(**args, relative_path=relative, patch=patch)
    assert path.read_bytes() == content
