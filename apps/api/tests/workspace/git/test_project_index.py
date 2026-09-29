"""index授权读取的隔离PG与临时目录验收；不运行Git。"""

import os
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import project_head as head, project_index as service, project_source as source
from tests.assertions import require_value
from tests.workspace.git.test_index_v2 import entry, fixture as index_bytes
from tests.workspace.git.test_project_head import ready, repository, setup, target

__all__ = ['ready', 'repository', 'setup', 'target']


@pytest.mark.parametrize('kind', ['normal', 'empty', 'missing'])
def test_read_only_observation_and_cleanup(setup, ready, engine, monkeypatch, kind):
    path = ready / 'index'
    raw = index_bytes(entry()) if kind == 'normal' else index_bytes()
    if kind != 'missing':
        path.write_bytes(raw)
    before = path.stat() if path.exists() else None
    descriptors, statements = [], []
    original = head._read
    def checked(fd, **kwargs):
        assert all(session.closed and not session.in_transaction() for session in setup[2])
        descriptors.append(fd)
        return original(fd, **kwargs)
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    monkeypatch.setattr(head, '_read', checked)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        result = service.read_project_git_index(setup[0])
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert len(statements) == 2
    assert all(sql.lstrip().lower().startswith('select') and 'for update' not in sql.lower() for sql in statements)
    assert result.status == ('index_missing' if kind == 'missing' else 'index_present')
    if kind == 'missing':
        assert result.index is None and not path.exists()
    else:
        assert result.index is not None
        assert len(result.index.entries) == (1 if kind == 'normal' else 0)
        assert result.index.byte_count == len(raw)
        assert path.read_bytes() == raw
        assert before is not None
        assert (path.stat().st_ino, path.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    pytest.raises(FrozenInstanceError, setattr, result, 'status', 'index_missing')
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('kind', ['foreign', 'stale', 'unbound'])
def test_authorization_before_index_io(setup, target, engine, monkeypatch, kind):
    reference = dict(setup[0])
    if kind == 'foreign':
        reference['user_id'] = target['other_id']
    else:
        with Session(engine) as session, session.begin():
            workspace = require_value(session.scalar(select(Workspace)))
            if kind == 'stale':
                workspace.binding_revision += 1
            else:
                workspace.root_path = None
    monkeypatch.setattr(service, '_observe_index', lambda *a: pytest.fail('authorize first'))
    with pytest.raises(WorkspaceNotAccessibleError if kind == 'foreign' else source.ProjectGitSourceError):
        service.read_project_git_index(reference)


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'directory', 'hardlink', 'oversize'])
def test_unsupported_file_rejected(setup, ready, kind):
    path = ready / 'index'
    if kind == 'symlink':
        path.symlink_to(ready / 'HEAD')
    elif kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'directory':
        path.mkdir()
    elif kind == 'hardlink':
        os.link(ready / 'HEAD', path)
    else:
        with path.open('wb') as stream:
            stream.truncate(service.MAX_INDEX_BYTES + 1)
    with pytest.raises(source.ProjectGitSourceError):
        service.read_project_git_index(setup[0])


@pytest.mark.parametrize('raw,code', [
    (b'', 'index_v2_invalid'),
    (index_bytes(version=3), 'index_v2_version_unsupported'),
    (index_bytes(extra=b'TREE\0\0\0\0'), 'index_v2_extensions_unsupported'),
    (index_bytes()[:-1] + b'x', 'index_v2_checksum_mismatch'),
])
def test_parser_failure_preserves_safe_code(setup, ready, raw, code):
    (ready / 'index').write_bytes(raw)
    with pytest.raises(source.ProjectGitSourceError) as caught:
        service.read_project_git_index(setup[0])
    assert caught.value.code == code


@pytest.mark.parametrize('change', ['replace', 'content', 'delete', 'link', 'create', 'config', 'revision', 'owner'])
def test_second_authorization_and_file_recheck(setup, ready, engine, target, monkeypatch, change):
    path = ready / 'index'
    if change != 'create':
        path.write_bytes(index_bytes(entry()))
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'replace':
                other = ready / 'replacement'
                other.write_bytes(path.read_bytes())
                other.replace(path)
            elif change in ('content', 'create'):
                path.write_bytes(index_bytes())
            elif change in ('delete', 'link'):
                path.unlink()
                if change == 'link':
                    path.symlink_to(ready / 'HEAD')
            elif change == 'config':
                (ready / 'config').write_bytes(b'[core]\nbare = true\n')
            else:
                with Session(engine) as session, session.begin():
                    if change == 'revision':
                        require_value(session.scalar(select(Workspace))).binding_revision += 1
                    else:
                        require_value(session.get(Conversation, target['conversation_pk'])).user_id = target['other_id']
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(WorkspaceNotAccessibleError if change == 'owner' else source.ProjectGitSourceError):
        service.read_project_git_index(setup[0])
    assert calls == 2


@pytest.mark.parametrize('kind', ['permission', 'interrupt', 'mutate', 'second-read', 'different-bytes'])
def test_read_failures_close_descriptors(setup, ready, monkeypatch, kind):
    path = ready / 'index'
    path.write_bytes(index_bytes(entry()))
    original = head._read
    descriptors = []
    def failed(fd, **kwargs):
        descriptors.append(fd)
        if kind == 'permission' or (kind == 'second-read' and len(descriptors) == 2):
            raise PermissionError('private index path')
        if kind == 'interrupt':
            raise KeyboardInterrupt()
        raw = original(fd, **kwargs)
        if kind == 'mutate':
            path.write_bytes(index_bytes())
        if kind == 'different-bytes' and len(descriptors) == 2:
            return index_bytes()
        return raw
    monkeypatch.setattr(head, '_read', failed)
    with pytest.raises(KeyboardInterrupt if kind == 'interrupt' else source.ProjectGitSourceError) as caught:
        service.read_project_git_index(setup[0])
    assert 'private index path' not in str(caught.value)
    assert descriptors
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_growth_during_bounded_read_rejected(setup, ready, monkeypatch):
    path = ready / 'index'
    raw = index_bytes(entry())
    path.write_bytes(raw)
    monkeypatch.setattr(service, 'MAX_INDEX_BYTES', len(raw))
    original = head.os.read
    descriptors = []
    def growing(fd, count):
        if os.fstat(fd).st_ino == path.stat().st_ino:
            descriptors.append(fd)
            if len(descriptors) == 1:
                with path.open('ab') as stream:
                    stream.write(b'x')
        return original(fd, count)
    monkeypatch.setattr(head.os, 'read', growing)
    with pytest.raises(source.ProjectGitSourceError):
        service.read_project_git_index(setup[0])
    assert descriptors
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('fail_at', [1, 3])
def test_absence_observation_failure_is_not_missing(setup, ready, monkeypatch, fail_at):
    original = head.os.stat
    calls = 0
    def denied(path, *args, **kwargs):
        nonlocal calls
        if path == 'index':
            calls += 1
            if calls == fail_at:
                raise PermissionError('cannot inspect private path')
        return original(path, *args, **kwargs)
    # 替换stat函数时同步能力集合，保留真实平台能力检查的含义。
    monkeypatch.setattr(head.os, 'supports_dir_fd', head.os.supports_dir_fd | {denied})
    monkeypatch.setattr(head.os, 'supports_follow_symlinks', head.os.supports_follow_symlinks | {denied})
    monkeypatch.setattr(head.os, 'stat', denied)
    with pytest.raises(source.ProjectGitSourceError) as caught:
        service.read_project_git_index(setup[0])
    assert caught.value.code == 'project_git_source_unavailable'
    assert calls == fail_at
