"""loose commit纯解析及隔离PG/临时目录观察，不运行Git。"""

import os
import zlib
from hashlib import sha1

import pytest

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import loose_commit as commit, project_head as head, project_source as source
from tests.workspace.git.test_project_head import ready, repository, setup, target

__all__ = ['ready', 'repository', 'setup', 'target']
TREE = 'a' * 40
PARENT = 'b' * 40
BODY = (f'tree {TREE}\nauthor Example <user@example.test> 123 +0800\n'
        'committer Example <user@example.test> 123 +0800\n\nmessage\n').encode()


def encoded(body=BODY, header=None):
    raw = (header if header is not None else f'commit {len(body)}'.encode()) + b'\0' + body
    return zlib.compress(raw), sha1(raw).hexdigest(), raw


def test_valid_commit_projects_only_bounded_facts():
    body = BODY.replace(b'author ', f'parent {PARENT}\nauthor '.encode(), 1) + '中文'.encode()
    compressed, oid, _ = encoded(body)
    result = commit.parse_loose_commit(compressed, expected_oid=oid)
    assert result.tree_id == TREE
    assert result.parent_ids == (PARENT,)
    assert result.body_bytes == len(body)
    assert result.message_bytes == len('message\n中文'.encode())
    assert 'user@example.test' not in repr(result)


@pytest.mark.parametrize('kind', ['invalid-zlib', 'truncated', 'trailing', 'concatenated', 'wrong-hash',
    'size', 'leading-zero', 'blob', 'missing-header', 'bad-oid', 'compressed-limit', 'expanded-limit'])
def test_envelope_failures(kind, monkeypatch):
    compressed, oid, raw = encoded()
    if kind == 'invalid-zlib':
        compressed = b'not compressed'
    elif kind == 'truncated':
        compressed = compressed[:-1]
    elif kind == 'trailing':
        compressed += b'extra'
    elif kind == 'concatenated':
        compressed += zlib.compress(b'other stream')
    elif kind == 'wrong-hash':
        oid = 'c' * 40
    elif kind in ('size', 'leading-zero', 'blob'):
        header = {'size': b'commit 1', 'leading-zero': b'commit 01', 'blob': f'blob {len(BODY)}'.encode()}[kind]
        compressed, oid, _ = encoded(header=header)
    elif kind == 'missing-header':
        compressed = zlib.compress(BODY)
        oid = sha1(BODY).hexdigest()
    elif kind == 'bad-oid':
        oid = '../outside'
    elif kind == 'compressed-limit':
        monkeypatch.setattr(commit, 'MAX_COMPRESSED_BYTES', len(compressed) - 1)
    else:
        monkeypatch.setattr(commit, 'MAX_OBJECT_BYTES', len(raw) - 1)
    with pytest.raises(source.ProjectGitSourceError):
        commit.parse_loose_commit(compressed, expected_oid=oid)


@pytest.mark.parametrize('body', [
    b'', b'garbage', BODY.replace(b'\n\n', b'\n', 1),
    BODY.replace(TREE.encode(), b'0' * 40), BODY.replace(b'tree ', b'other ', 1),
    BODY.replace(b'author ', b'committer ', 1), BODY.replace(b'123 +0800', b'bad date'),
    BODY.replace(b'+0800', b'+9960'), BODY.replace(b' <', b' ', 1),
    BODY.replace(b'\n\n', b'\ngpgsig signature\n\n', 1),
    BODY.replace(b'\n\n', b'\nencoding latin1\n\n', 1), BODY + b'\0', BODY + b'\xff',
    BODY.replace(b'author ', (f'parent {PARENT}\n' * 2 + 'author ').encode(), 1),
    BODY.replace(b'author ', b'parent invalid\nauthor ', 1),
])
def test_commit_structure_or_unsupported_features_rejected(body):
    compressed, oid, _ = encoded(body)
    with pytest.raises(source.ProjectGitSourceError):
        commit.parse_loose_commit(compressed, expected_oid=oid)


def test_exact_limits_and_parent_budget(monkeypatch):
    compressed, oid, raw = encoded()
    monkeypatch.setattr(commit, 'MAX_COMPRESSED_BYTES', len(compressed))
    monkeypatch.setattr(commit, 'MAX_OBJECT_BYTES', len(raw))
    assert commit.parse_loose_commit(compressed, expected_oid=oid).object_id == oid
    monkeypatch.setattr(commit, 'MAX_COMMIT_HEADER_BYTES', 1)
    with pytest.raises(source.ProjectGitSourceError):
        commit.parse_loose_commit(compressed, expected_oid=oid)


def test_decompression_bomb_and_parent_limit(monkeypatch):
    compressed, oid, _ = encoded(BODY + b'x' * (commit.MAX_OBJECT_BYTES + 1))
    assert len(compressed) < commit.MAX_COMPRESSED_BYTES
    with pytest.raises(source.ProjectGitSourceError) as caught:
        commit.parse_loose_commit(compressed, expected_oid=oid)
    assert caught.value.code == 'project_git_commit_limit'
    monkeypatch.setattr(commit, 'MAX_COMMIT_PARENTS', 0)
    compressed, oid, _ = encoded(BODY.replace(b'author ', f'parent {PARENT}\nauthor '.encode(), 1))
    with pytest.raises(source.ProjectGitSourceError):
        commit.parse_loose_commit(compressed, expected_oid=oid)


@pytest.fixture
def object_file(ready):
    compressed, oid, _ = encoded()
    path = ready / 'objects' / oid[:2] / oid[2:]
    path.parent.mkdir()
    path.write_bytes(compressed)
    (ready / 'refs/heads/main').write_text(oid + '\n')
    return path, oid


@pytest.mark.parametrize('origin', ['loose', 'packed', 'detached'])
def test_real_commit_read_with_live_scope(setup, ready, object_file, monkeypatch, origin):
    path, oid = object_file
    if origin == 'packed':
        (ready / 'refs/heads/main').unlink()
        (ready / 'packed-refs').write_text(f'{oid} refs/heads/main\n')
    elif origin == 'detached':
        (ready / 'HEAD').write_text(oid + '\n')
    original = head._read
    descriptors = []
    def checked(fd, **kwargs):
        assert all(session.closed and not session.in_transaction() for session in setup[2])
        descriptors.append(fd)
        return original(fd, **kwargs)
    monkeypatch.setattr(head, '_read', checked)
    before = path.read_bytes(), path.stat().st_mtime_ns
    result = commit.read_head_loose_commit(setup[0])
    assert result.status == 'commit_verified'
    assert result.commit is not None and result.commit.object_id == oid
    assert result.commit.tree_id == TREE  # 不需要tree/parent对象存在，也不读取它们。
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_unborn_does_not_invent_or_read_object(setup, ready, monkeypatch):
    monkeypatch.setattr(commit, 'observe_object', lambda *a, **kw: pytest.fail('no object ID'))
    result = commit.read_head_loose_commit(setup[0])
    assert result.status == 'no_head_object_observed' and result.commit is None


def test_missing_loose_does_not_claim_nonexistent_commit(setup, ready, object_file):
    object_file[0].unlink()
    (ready / 'objects/pack').mkdir()
    (ready / 'objects/pack/example.pack').write_bytes(b'not inspected')
    with pytest.raises(source.ProjectGitSourceError, match='git_pack_invalid'):
        commit.read_head_loose_commit(setup[0])


@pytest.mark.parametrize('change', ['replace', 'content', 'delete', 'link', 'head'])
def test_mutations_during_second_authorization_fail(setup, ready, object_file, monkeypatch, change):
    path = object_file[0]
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'replace':
                other = path.with_name('replacement')
                other.write_bytes(path.read_bytes())
                other.replace(path)
            elif change == 'content':
                path.write_bytes(b'changed')
            elif change == 'head':
                (ready / 'HEAD').write_text('c' * 40 + '\n')
            else:
                path.unlink()
                if change == 'link':
                    path.symlink_to(ready / 'HEAD')
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        commit.read_head_loose_commit(setup[0])


@pytest.mark.parametrize('kind', ['permission', 'interrupt', 'mutate', 'oversize'])
def test_object_read_failure_and_cleanup(setup, ready, object_file, monkeypatch, kind):
    path = object_file[0]
    inode = path.stat().st_ino
    original = head._read
    descriptors = []
    def failed(fd, **kwargs):
        if os.fstat(fd).st_ino == inode:
            descriptors.append(fd)
            if kind == 'permission':
                raise PermissionError('private object path')
            if kind == 'interrupt':
                raise KeyboardInterrupt()
            raw = original(fd, **kwargs)
            path.write_bytes(b'changed')
            return raw
        return original(fd, **kwargs)
    monkeypatch.setattr(head, '_read', failed)
    if kind == 'oversize':
        path.write_bytes(b'x' * (commit.MAX_COMPRESSED_BYTES + 1))
    with pytest.raises(KeyboardInterrupt if kind == 'interrupt' else source.ProjectGitSourceError):
        commit.read_head_loose_commit(setup[0])
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_authorization_precedes_object_read(setup, target, monkeypatch):
    monkeypatch.setattr(commit, '_observe_commit', lambda *a: pytest.fail('must authorize'))
    with pytest.raises(WorkspaceNotAccessibleError):
        commit.read_head_loose_commit({**setup[0], 'user_id': target['other_id']})
