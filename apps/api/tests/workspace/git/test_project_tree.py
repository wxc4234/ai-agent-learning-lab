"""真实隔离PG与临时对象目录；不执行Git，也不连接开发业务表。"""

import os

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import project_head as head, project_source as source, project_tree as service
from tests.assertions import require_value
from tests.workspace.git.test_loose_commit import BODY, TREE, encoded as commit_bytes
from tests.workspace.git.test_loose_tree import encoded as tree_bytes, entry
from tests.workspace.git.test_project_head import ready, repository, setup, target

__all__ = ['ready', 'repository', 'setup', 'target']


def install(ready, body):
    compressed, oid, _ = tree_bytes(body)
    tree_path = ready / 'objects' / oid[:2] / oid[2:]
    tree_path.parent.mkdir(exist_ok=True)
    tree_path.write_bytes(compressed)
    commit_raw, commit_oid, _ = commit_bytes(BODY.replace(TREE.encode(), oid.encode()))
    commit_path = ready / 'objects' / commit_oid[:2] / commit_oid[2:]
    commit_path.parent.mkdir(exist_ok=True)
    commit_path.write_bytes(commit_raw)
    (ready / 'refs/heads/main').write_text(commit_oid + '\n')
    return tree_path, commit_path


@pytest.fixture
def objects(ready):
    return install(ready, entry())


@pytest.mark.parametrize('kind', ['normal', 'empty', 'detached', 'packed'])
def test_same_scope_read_only_observation(setup, ready, engine, monkeypatch, kind):
    tree_path, commit_path = install(ready, b'' if kind == 'empty' else entry())
    oid = commit_path.parent.name + commit_path.name
    if kind == 'detached':
        (ready / 'HEAD').write_text(oid + '\n')
    elif kind == 'packed':
        (ready / 'refs/heads/main').unlink()
        (ready / 'packed-refs').write_text(f'{oid} refs/heads/main\n')
    original = head._read
    descriptors, statements = [], []
    before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (tree_path, commit_path)]
    def checked(fd, **kwargs):
        assert all(s.closed and not s.in_transaction() for s in setup[2])
        descriptors.append(fd)
        if os.fstat(fd).st_ino == tree_path.stat().st_ino:
            assert any(os.fstat(other).st_ino == commit_path.stat().st_ino for other in descriptors)
        return original(fd, **kwargs)
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    monkeypatch.setattr(head, '_read', checked)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        result = service.read_head_loose_tree(setup[0])
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert len(statements) == 2 and all(s.lower().startswith('select') for s in statements)
    assert result.status == 'tree_verified'
    assert result.tree is not None and len(result.tree.entries) == (0 if kind == 'empty' else 1)
    assert result.commit.commit is not None and result.commit.commit.tree_id == result.tree.object_id
    assert before == [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (tree_path, commit_path)]
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('missing', ['head', 'commit', 'tree', 'tree-directory'])
def test_missing_is_not_empty_tree(setup, ready, objects, missing):
    if missing == 'head':
        (ready / 'refs/heads/main').unlink()
    elif missing == 'commit':
        objects[1].unlink()
    else:
        objects[0].unlink()
        if missing == 'tree-directory':
            objects[0].parent.rmdir()
    result = service.read_head_loose_tree(setup[0])
    assert result.tree is None
    assert result.status == {'head': 'no_head_object_observed', 'commit': 'commit_object_missing',
                             'tree': 'tree_object_missing',
                             'tree-directory': 'tree_object_missing'}[missing]


@pytest.mark.parametrize('kind', ['foreign', 'stale'])
def test_authorize_before_io(setup, target, monkeypatch, kind):
    request = dict(setup[0])
    request.update({'user_id': target['other_id']} if kind == 'foreign' else {'binding_revision': 2})
    monkeypatch.setattr(service, '_observe_tree', lambda *a: pytest.fail('authorize first'))
    with pytest.raises(WorkspaceNotAccessibleError if kind == 'foreign' else source.ProjectGitSourceError):
        service.read_head_loose_tree(request)


@pytest.mark.parametrize('change', ['replace', 'content', 'delete', 'create', 'commit', 'head', 'config', 'revision', 'owner'])
def test_changes_at_second_authorization_reject(setup, ready, objects, engine, target, monkeypatch, change):
    path = objects[0]
    raw = path.read_bytes()
    if change == 'create':
        path.unlink()
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'replace':
                replacement = path.with_name('replacement')
                replacement.write_bytes(raw)
                replacement.replace(path)
            elif change == 'create':
                path.write_bytes(raw)
            elif change == 'delete':
                path.unlink()
            elif change in ('content', 'commit', 'head', 'config'):
                selected = {'content': path, 'commit': objects[1], 'head': ready / 'HEAD', 'config': ready / 'config'}
                selected[change].write_bytes(b'changed')
            else:
                with Session(engine) as session, session.begin():
                    if change == 'revision':
                        require_value(session.scalar(select(Workspace))).binding_revision += 1
                    else:
                        require_value(session.get(Conversation, target['conversation_pk'])).user_id = target['other_id']
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(WorkspaceNotAccessibleError if change == 'owner' else source.ProjectGitSourceError):
        service.read_head_loose_tree(setup[0])
    assert calls == 2


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'hardlink', 'oversize', 'corrupt', 'wrong-hash'])
def test_invalid_tree_file(setup, ready, objects, kind):
    path = objects[0]
    path.unlink()
    if kind == 'symlink':
        path.symlink_to(objects[1])
    elif kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'hardlink':
        os.link(objects[1], path)
    elif kind == 'oversize':
        with path.open('wb') as stream:
            stream.truncate(service.MAX_COMPRESSED_BYTES + 1)
    else:
        path.write_bytes(b'bad' if kind == 'corrupt' else tree_bytes()[0])
    with pytest.raises(source.ProjectGitSourceError) as caught:
        service.read_head_loose_tree(setup[0])
    if kind in ('corrupt', 'wrong-hash'):
        assert caught.value.code == ('loose_tree_compression_invalid' if kind == 'corrupt' else 'loose_tree_hash_mismatch')


@pytest.mark.parametrize('kind', ['permission', 'interrupt', 'second-read', 'mutate'])
def test_failed_reads_release_entire_chain(setup, ready, objects, monkeypatch, kind):
    original = head._read
    descriptors = []
    calls = 0
    inode = objects[0].stat().st_ino
    def failed(fd, **kwargs):
        nonlocal calls
        descriptors.append(fd)
        if os.fstat(fd).st_ino == inode:
            calls += 1
            if kind == 'permission' or (kind == 'second-read' and calls == 2):
                raise PermissionError('private path')
            if kind == 'interrupt':
                raise KeyboardInterrupt()
            raw = original(fd, **kwargs)
            if kind == 'mutate':
                objects[0].write_bytes(b'changed')
            return raw
        return original(fd, **kwargs)
    monkeypatch.setattr(head, '_read', failed)
    with pytest.raises(KeyboardInterrupt if kind == 'interrupt' else source.ProjectGitSourceError) as caught:
        service.read_head_loose_tree(setup[0])
    assert 'private path' not in str(caught.value)
    assert calls > 0
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)
