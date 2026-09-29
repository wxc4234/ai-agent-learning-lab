"""隔离PG/临时目录验证同作用域两侧观察，不执行Git或操作开发表。"""

import os

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import project_head as head, project_source as source, project_staged as service
from tests.assertions import require_value
from tests.workspace.git.test_index_v2 import entry as index_entry, fixture as index_bytes
from tests.workspace.git.test_loose_tree import entry as tree_entry
from tests.workspace.git.test_project_tree import install, ready, repository, setup, target
from tests.workspace.git.test_tree_graph import put

__all__ = ['ready', 'repository', 'setup', 'target']


@pytest.fixture
def files(ready):
    oid, child = put(ready, tree_entry(b'file', oid=b'\x12' * 20))
    root, commit = install(ready, tree_entry(b'dir', b'40000', bytes.fromhex(oid)))
    index = ready / 'index'
    index.write_bytes(index_bytes(index_entry(b'dir/file')))
    return root, child, commit, index


@pytest.mark.parametrize('kind', ['equal', 'modified', 'conflict', 'empty'])
def test_combined_scope_and_read_only_result(setup, ready, files, engine, monkeypatch, kind):
    if kind == 'modified':
        files[3].write_bytes(index_bytes(index_entry(b'dir/file', oid=b'\x34' * 20)))
    elif kind == 'conflict':
        files[3].write_bytes(index_bytes(index_entry(b'dir/file', stage=2), index_entry(b'dir/file', stage=3)))
    elif kind == 'empty':
        install(ready, b'')
        files[3].write_bytes(index_bytes())
    original = head._read
    fds, statements = [], []
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
    def checked(fd, **kw):
        assert all(s.closed and not s.in_transaction() for s in setup[2])
        fds.append(fd)
        if kind != 'empty' and os.fstat(fd).st_ino == files[3].stat().st_ino:
            held = {os.fstat(other).st_ino for other in fds}
            assert all(p.stat().st_ino in held for p in files[:3])
        return original(fd, **kw)
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    monkeypatch.setattr(head, '_read', checked)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        result = service.read_project_staged(setup[0])
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert result.status == 'staged_compared' and result.unavailable_reasons == ()
    assert result.head.commit.head.config is result.index.config
    assert result.changes is not None
    assert [x.status for x in result.changes] == ({'modified': ['modified'], 'conflict': ['unmerged']}.get(kind, []))
    assert len(statements) == 2 and all(s.lower().startswith('select') for s in statements)
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
    for fd in set(fds):
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('kind', ['head', 'commit', 'index', 'both'])
def test_unavailable_does_not_become_empty_comparison(setup, ready, files, monkeypatch, kind):
    if kind in ('head', 'both'):
        (ready / 'refs/heads/main').unlink()
    if kind == 'commit':
        files[2].unlink()
    if kind in ('index', 'both'):
        files[3].unlink()
    monkeypatch.setattr(service, 'compare_staged', lambda *a: pytest.fail('missing input'))
    result = service.read_project_staged(setup[0])
    assert result.status == 'comparison_unavailable' and result.changes is None
    assert result.unavailable_reasons == {
        'head': ('no_head_object_observed',), 'commit': ('commit_object_missing',),
        'index': ('index_missing',), 'both': ('no_head_object_observed', 'index_missing'),
    }[kind]


@pytest.mark.parametrize('kind', ['root', 'child', 'index-corrupt', 'tree-corrupt', 'index-link', 'index-oversize'])
def test_invalid_or_missing_tree_and_bad_index_fail(setup, files, kind):
    if kind in ('root', 'child'):
        files[0 if kind == 'root' else 1].unlink()
    elif kind == 'tree-corrupt':
        files[1].write_bytes(b'bad')
    elif kind == 'index-link':
        files[3].unlink()
        files[3].symlink_to(files[0])
    elif kind == 'index-oversize':
        with files[3].open('wb') as stream:
            stream.truncate(service.MAX_INDEX_BYTES + 1)
    else:
        files[3].write_bytes(b'bad')
    with pytest.raises(source.ProjectGitSourceError):
        service.read_project_staged(setup[0])


@pytest.mark.parametrize('kind', ['index', 'index-replace', 'index-create', 'child', 'root', 'commit', 'head', 'config', 'revision'])
def test_second_authorization_rechecks_both_sides(setup, ready, files, engine, monkeypatch, kind):
    raw = files[3].read_bytes()
    if kind == 'index-create':
        files[3].unlink()
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kw):
        nonlocal calls
        calls += 1
        if calls == 2:
            if kind == 'index-create':
                files[3].write_bytes(raw)
            elif kind == 'index-replace':
                replacement = ready / 'replacement'
                replacement.write_bytes(raw)
                replacement.replace(files[3])
            elif kind == 'revision':
                with Session(engine) as session, session.begin():
                    require_value(session.scalar(select(Workspace))).binding_revision += 1
            else:
                path = {'index': files[3], 'child': files[1], 'root': files[0], 'commit': files[2],
                        'head': ready / 'HEAD', 'config': ready / 'config'}[kind]
                path.write_bytes(b'changed')
        return original(*args, **kw)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        service.read_project_staged(setup[0])
    assert calls == 2


@pytest.mark.parametrize('kind', ['foreign', 'stale'])
def test_authorization_first(setup, target, monkeypatch, kind):
    request = dict(setup[0])
    request.update({'user_id': target['other_id']} if kind == 'foreign' else {'binding_revision': 2})
    monkeypatch.setattr(service, '_observe_staged', lambda *a: pytest.fail('authorize first'))
    with pytest.raises(WorkspaceNotAccessibleError if kind == 'foreign' else source.ProjectGitSourceError):
        service.read_project_staged(request)


@pytest.mark.parametrize('kind', ['permission', 'interrupt', 'second-read', 'compare-limit'])
def test_failure_closes_entire_observation(setup, files, monkeypatch, kind):
    original = head._read
    fds = []
    calls = 0
    def failed(fd, **kw):
        nonlocal calls
        fds.append(fd)
        if os.fstat(fd).st_ino == files[3].stat().st_ino:
            calls += 1
            if kind == 'interrupt':
                raise KeyboardInterrupt()
            if kind == 'permission' or (kind == 'second-read' and calls == 2):
                raise PermissionError('private path')
        return original(fd, **kw)
    monkeypatch.setattr(head, '_read', failed)
    if kind == 'compare-limit':
        from app.services.workspace.git import staged_compare
        monkeypatch.setattr(staged_compare, 'MAX_PATH_TOTAL', 1)
    with pytest.raises(KeyboardInterrupt if kind == 'interrupt' else source.ProjectGitSourceError) as caught:
        service.read_project_staged(setup[0])
    assert 'private path' not in str(caught.value)
    if kind == 'compare-limit':
        assert isinstance(caught.value, source.ProjectGitSourceError)
        assert caught.value.code == 'staged_compare_limit'
    assert calls > 0
    for fd in set(fds):
        with pytest.raises(OSError):
            os.fstat(fd)
