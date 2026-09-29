"""HEAD子集解析与隔离PG/临时目录观察，不运行Git或读取对象。"""

import os

import pytest

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import project_head as head, project_source as source
from tests.workspace.git.test_project_config import VALID
from tests.workspace.git.test_project_layout import repository, setup, target

__all__ = ['repository', 'setup', 'target']
OID = 'a1' * 20


@pytest.mark.parametrize('name', ['main', 'feature/topic-1', 'v1.2', 'under_score'])
def test_supported_branch_names(name):
    assert head.parse_head(f'ref: refs/heads/{name}\n'.encode()) == ('refs/heads/' + name, None)


@pytest.mark.parametrize('name', ['', '../escape', '.hidden', 'a..b', 'a.lock', 'a/b.lock', 'a.',
    'a//b', '/abs', 'a\\b', 'a b', 'a@{1}', 'a~1', 'a^', 'a?', 'a*', 'a[', 'a:b', '中文',
    'a\nb', 'a\r', '/'.join(['a'] * 16)])
def test_unsafe_or_unsupported_branches(name):
    with pytest.raises(source.ProjectGitSourceError):
        head.parse_head(f'ref: refs/heads/{name}\n'.encode())


@pytest.mark.parametrize('raw', [b'', b'0' * 40, b'a' * 39, b'a' * 41, b'A' * 40,
    b'a' * 64, OID.encode() + b'\r\n', OID.encode() + b'\n\n', b'\xff',
    b'ref: refs/tags/main\n', b'ref: /outside\n', b'ref: refs/heads/main\x00'])
def test_invalid_head_formats(raw):
    with pytest.raises(source.ProjectGitSourceError):
        head.parse_head(raw)


def test_detached_and_byte_budget():
    assert head.parse_head(OID.encode()) == (None, OID)
    assert head.parse_object_id(OID.encode() + b'\n') == OID
    with pytest.raises(source.ProjectGitSourceError) as caught:
        head.parse_head(b'x' * (head.MAX_REF_BYTES + 1))
    assert caught.value.code == 'project_git_head_limit'


@pytest.fixture
def ready(repository):
    (repository / 'config').write_bytes(VALID)
    return repository


@pytest.mark.parametrize('kind', ['branch', 'nested', 'detached', 'unborn', 'missing-parent'])
def test_real_states_without_object_existence_claim(setup, ready, kind, monkeypatch):
    if kind == 'detached':
        (ready / 'HEAD').write_text(OID + '\n')
    elif kind in ('branch', 'nested'):
        name = 'topic/main' if kind == 'nested' else 'main'
        target_path = ready / 'refs/heads' / name
        target_path.parent.mkdir(exist_ok=True)
        target_path.write_text(OID + '\n')
        (ready / 'HEAD').write_text('ref: refs/heads/' + name + '\n')
    elif kind == 'missing-parent':
        (ready / 'HEAD').write_text('ref: refs/heads/new/topic\n')
    read = head._read
    descriptors = []
    def checked(fd):
        assert all(session.closed and not session.in_transaction() for session in setup[2])
        descriptors.append(fd)
        return read(fd)
    monkeypatch.setattr(head, '_read', checked)
    result = head.read_project_git_head(setup[0])
    expected = 'unborn_candidate' if kind in ('unborn', 'missing-parent') else 'branch' if kind == 'nested' else kind
    assert result.head.state == expected
    assert result.head.object_id == (None if expected == 'unborn_candidate' else OID)
    assert list((ready / 'objects/info').iterdir()) == []
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('kind', ['empty-ref', 'symref', 'link', 'fifo', 'oversize'])
def test_not_unborn_when_evidence_unsupported(setup, ready, kind):
    ref = ready / 'refs/heads/main'
    if kind == 'empty-ref':
        ref.touch()
    elif kind == 'symref':
        ref.write_text('ref: refs/heads/other\n')
    elif kind == 'link':
        ref.symlink_to(ready / 'HEAD')
    elif kind == 'fifo':
        os.mkfifo(ref)
    else:
        ref.write_bytes(b'x' * (head.MAX_REF_BYTES + 1))
    with pytest.raises(source.ProjectGitSourceError):
        head.read_project_git_head(setup[0])


@pytest.mark.parametrize('change', ['head', 'target', 'replace', 'create-ref', 'create-parent', 'packed'])
def test_changes_across_second_query_rejected(setup, ready, monkeypatch, change):
    ref = ready / 'refs/heads/main'
    if change == 'create-parent':
        (ready / 'HEAD').write_text('ref: refs/heads/new/topic\n')
    elif change != 'create-ref':
        ref.write_text(OID + '\n')
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'head':
                (ready / 'HEAD').write_text(OID + '\n')
            elif change == 'replace':
                replacement = ready / 'replacement'
                replacement.write_text(OID + '\n')
                replacement.replace(ref)
            elif change == 'create-parent':
                (ready / 'refs/heads/new').mkdir()
            elif change == 'packed':
                (ready / 'packed-refs').touch()
            else:
                ref.write_text('b' * 40 + '\n')
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        head.read_project_git_head(setup[0])


@pytest.mark.parametrize('kind', ['permission', 'interrupt', 'mutate'])
def test_read_failures_and_interrupt_cleanup(setup, ready, monkeypatch, kind):
    descriptors = []
    original = head._read
    def failed(fd):
        descriptors.append(fd)
        if kind == 'interrupt':
            raise KeyboardInterrupt()
        if kind == 'permission':
            raise PermissionError('secret path')
        raw = original(fd)
        (ready / 'HEAD').write_text(OID + '\n')
        return raw
    monkeypatch.setattr(head, '_read', failed)
    with pytest.raises(KeyboardInterrupt if kind == 'interrupt' else source.ProjectGitSourceError):
        head.read_project_git_head(setup[0])
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_authorization_before_head_read(setup, target, monkeypatch):
    monkeypatch.setattr(head, '_observe_head', lambda fd: pytest.fail('must not read'))
    with pytest.raises(WorkspaceNotAccessibleError):
        head.read_project_git_head({**setup[0], 'user_id': target['other_id']})
