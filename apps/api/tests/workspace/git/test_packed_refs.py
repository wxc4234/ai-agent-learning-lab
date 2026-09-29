"""packed解析及HEAD查找：纯格式测试和隔离PostgreSQL/临时文件集成。"""

import os

import pytest

from app.services.workspace.git import packed_refs as parser, project_head as head, project_source as source
from tests.workspace.git.test_project_head import OID, ready, repository, setup, target

__all__ = ['ready', 'repository', 'setup', 'target']
OTHER = 'b' * 40
HEADER = b'# pack-refs with: peeled fully-peeled sorted \n'


def line(name='refs/heads/main', oid=OID):
    return f'{oid} {name}\n'.encode()


@pytest.mark.parametrize('prefix', [b'', HEADER, b'# pack-refs with: \n', b'# pack-refs with: fully-peeled\n'])
def test_supported_headers_and_peeled(prefix):
    records = parser.parse_packed_refs(prefix + line() + b'^' + OTHER.encode() + b'\n')
    assert records == (parser.PackedReference('refs/heads/main', OID, OTHER),)


def test_empty_and_header_only_and_unsorted_without_claim():
    assert parser.parse_packed_refs(b'') == ()
    assert parser.parse_packed_refs(HEADER) == ()
    rows = parser.parse_packed_refs(line('refs/tags/v1') + line('refs/heads/main'))
    assert [row.name for row in rows] == ['refs/tags/v1', 'refs/heads/main']


@pytest.mark.parametrize('raw', [
    b'\n', b'\xff\n', b'\xef\xbb\xbf' + HEADER, HEADER.replace(b'\n', b'\r\n'),
    b'# unknown\n', b'# pack-refs with: unknown\n', b'# pack-refs with: sorted sorted\n',
    HEADER + HEADER, line() + line(), HEADER + line('refs/tags/v1') + line(),
    b'^' + OID.encode() + b'\n', line() + b'^' + OID.encode() + b'\n^' + OTHER.encode() + b'\n',
    line() + b'^bad\n', line(oid='0' * 40), line(oid='a' * 64), line(oid='A' * 40),
    line('refs/heads/../outside'), line('refs/heads/.hidden'), line('refs/heads/a.lock'),
    line('refs/heads/a..b'), line('refs/heads/a//b'), line('refs/heads/a\\b'),
    line('refs/heads/a b'), line('refs/heads/a@{1}'), line('refs/heads/a.'),
    line('HEAD'), line('refs/x'), line('refs/heads/中文'), line() + b'broken trailing record\n',
    line('refs/heads/a') + line('refs/heads/a-b') + line('refs/heads/a/b'),
])
def test_malformed_or_unsupported_input_has_no_partial_result(raw):
    with pytest.raises(source.ProjectGitSourceError):
        parser.parse_packed_refs(raw)


def test_truncation_and_limits(monkeypatch):
    with pytest.raises(source.ProjectGitSourceError) as caught:
        parser.parse_packed_refs(line()[:-1])
    assert caught.value.code == 'project_git_packed_incomplete'
    monkeypatch.setattr(parser, 'MAX_PACKED_BYTES', len(line()))
    assert len(parser.parse_packed_refs(line())) == 1
    with pytest.raises(source.ProjectGitSourceError):
        parser.parse_packed_refs(line() + b'\n')


def test_record_and_line_budgets(monkeypatch):
    monkeypatch.setattr(parser, 'MAX_PACKED_REFS', 1)
    assert len(parser.parse_packed_refs(line())) == 1
    with pytest.raises(source.ProjectGitSourceError):
        parser.parse_packed_refs(line() + line('refs/heads/other'))
    with pytest.raises(source.ProjectGitSourceError):
        parser.parse_packed_refs(b'#' * (parser.MAX_PACKED_LINE_BYTES + 1) + b'\n')


@pytest.mark.parametrize('kind', ['packed', 'loose', 'empty', 'other', 'peeled', 'detached'])
def test_lookup_semantics(setup, ready, monkeypatch, kind):
    path = ready / 'packed-refs'
    path.write_bytes(HEADER + line())
    if kind == 'loose':
        (ready / 'refs/heads/main').write_text(OTHER + '\n')
    elif kind == 'empty':
        path.write_bytes(b'')
    elif kind == 'other':
        path.write_bytes(HEADER + line('refs/tags/v1'))
    elif kind == 'peeled':
        path.write_bytes(HEADER + line() + b'^' + OTHER.encode() + b'\n')
    elif kind == 'detached':
        (ready / 'HEAD').write_text(OTHER + '\n')
    read = head._read
    descriptors = []
    def checked(fd, **kwargs):
        assert all(session.closed and not session.in_transaction() for session in setup[2])
        descriptors.append(fd)
        return read(fd, **kwargs)
    monkeypatch.setattr(head, '_read', checked)
    result = head.read_project_git_head(setup[0]).head
    expected_origin = {'packed': 'packed', 'loose': 'loose', 'empty': 'missing',
                       'other': 'missing', 'peeled': 'packed', 'detached': 'head'}[kind]
    assert result.origin == expected_origin
    assert result.object_id == (OTHER if kind in ('loose', 'detached') else None if kind in ('empty', 'other') else OID)
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_loose_does_not_hide_bad_packed_tail(setup, ready):
    (ready / 'refs/heads/main').write_text(OID + '\n')
    (ready / 'packed-refs').write_bytes(line() + b'invalid\n')
    with pytest.raises(source.ProjectGitSourceError):
        head.read_project_git_head(setup[0])


@pytest.mark.parametrize('change', ['replace', 'content', 'delete', 'link', 'create-loose'])
def test_changes_during_authorization_rejected(setup, ready, monkeypatch, change):
    path = ready / 'packed-refs'
    path.write_bytes(HEADER + line())
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'replace':
                other = ready / 'replacement'
                other.write_bytes(HEADER + line())
                other.replace(path)
            elif change == 'content':
                path.write_bytes(HEADER + line(oid=OTHER))
            elif change == 'create-loose':
                (ready / 'refs/heads/main').write_text(OTHER + '\n')
            else:
                path.unlink()
                if change == 'link':
                    path.symlink_to(ready / 'HEAD')
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        head.read_project_git_head(setup[0])


@pytest.mark.parametrize('kind', ['read', 'interrupt', 'change', 'oversize', 'grow'])
def test_packed_read_failures_are_not_absence(setup, ready, monkeypatch, kind):
    path = ready / 'packed-refs'
    path.write_bytes(HEADER + line())
    original = head._read
    descriptors = []
    def failed(fd, **kwargs):
        if kwargs.get('limit') == parser.MAX_PACKED_BYTES:
            descriptors.append(fd)
            if kind == 'interrupt':
                raise KeyboardInterrupt()
            if kind == 'read':
                raise PermissionError('private path')
            if kind == 'grow':
                path.write_bytes(b'x' * (parser.MAX_PACKED_BYTES + 1))
            data = original(fd, **kwargs)
            if kind == 'change':
                path.write_bytes(line(oid=OTHER))
            return data
        return original(fd, **kwargs)
    monkeypatch.setattr(head, '_read', failed)
    if kind == 'oversize':
        path.write_bytes(b'x' * (parser.MAX_PACKED_BYTES + 1))
    with pytest.raises(KeyboardInterrupt if kind == 'interrupt' else source.ProjectGitSourceError):
        head.read_project_git_head(setup[0])
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
