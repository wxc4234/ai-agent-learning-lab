"""真实隔离PG验证pack回退读取和退出复核。"""
import zlib

import pytest

from app.services.workspace.git import project_source as source, tree_graph as graph
from tests.workspace.git.test_project_staged import files, ready, repository, setup, target
from tests.workspace.git.test_pack_objects import build, header

__all__ = ['files', 'ready', 'repository', 'setup', 'target']


@pytest.fixture
def packed(files, ready):
    records = []
    for path in files[:3]:
        raw = zlib.decompress(path.read_bytes())
        prefix, body = raw.split(b'\0', 1)
        kind = 1 if prefix.startswith(b'commit ') else 2
        records.append(header(kind, len(body)) + zlib.compress(body))
    raw = build(*records)
    directory = ready / 'objects/pack'
    directory.mkdir()
    path = directory / f'pack-{raw[-20:].hex()}.pack'
    path.write_bytes(raw)
    for obj in files[:3]:
        obj.unlink()
    return path


def test_packed_head_and_shared_scope(setup, packed):
    result = graph.read_head_tree_graph(setup[0])
    assert result.leaves is not None and [x.path for x in result.leaves] == ['dir/file']


@pytest.mark.parametrize('kind', ['modify', 'replace', 'delete'])
def test_pack_changes_after_authorization_reject(setup, packed, monkeypatch, kind):
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kw):
        nonlocal calls
        calls += 1
        if calls == 2:
            if kind == 'modify':
                packed.write_bytes(b'changed')
            elif kind == 'delete':
                packed.unlink()
            else:
                temp = packed.with_suffix('.tmp')
                temp.write_bytes(packed.read_bytes())
                temp.replace(packed)
        return original(*args, **kw)
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        graph.read_head_tree_graph(setup[0])


def test_pack_checksum_must_match_filename(setup, packed):
    packed.rename(packed.with_name('pack-' + '0' * 40 + '.pack'))
    with pytest.raises(source.ProjectGitSourceError, match='git_pack_invalid'):
        graph.read_head_tree_graph(setup[0])
