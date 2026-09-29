"""叶对象有界解析：不存在、类型错误、损坏不当作有效文件。"""
import zlib
from hashlib import sha1
import pytest
from app.services.workspace.git.leaf_objects import parse_blob, MAX_OBJECT_BYTES
from app.services.workspace.git.project_source import ProjectGitSourceError

@pytest.mark.parametrize('body', [b'', b'hello\n', b'\0binary\xff', b'link-target'])
def test_valid_blob_preserves_bytes(body):
    raw = b'blob ' + str(len(body)).encode() + b'\0' + body
    assert parse_blob(zlib.compress(raw), sha1(raw).hexdigest()) == body

@pytest.mark.parametrize('raw', [b'blob 9\0x', b'tree 1\0x', b'blob 01\0x', b'blob 1\0xx'])
def test_bad_envelope(raw):
    with pytest.raises(ProjectGitSourceError):
        parse_blob(zlib.compress(raw), sha1(raw).hexdigest())

@pytest.mark.parametrize('kind', ['hash', 'truncated', 'trailing', 'bomb'])
def test_compression_and_hash(kind):
    raw = zlib.compress(b'blob 1\0x')
    oid = sha1(b'blob 1\0x').hexdigest()
    if kind == 'hash': oid = '0' * 40
    elif kind == 'truncated': raw = raw[:-1]
    elif kind == 'trailing': raw += b'extra'
    else: raw = zlib.compress(b'x' * (MAX_OBJECT_BYTES + 1))
    with pytest.raises(ProjectGitSourceError): parse_blob(raw, oid)

from tests.workspace.git.test_project_staged import ready, repository, setup, target
from tests.workspace.git.test_project_tree import install
from tests.workspace.git.test_loose_tree import entry as tree_entry
from tests.workspace.git.test_index_v2 import entry as index_entry, fixture as index_bytes
from app.services.workspace.git.leaf_objects import read_verified_project_objects
__all__ = ['ready', 'repository', 'setup', 'target']

@pytest.mark.parametrize('missing', [False, True])
def test_head_and_index_blob_scope(setup, ready, missing):
    raw = b'blob 5\0hello'
    oid = sha1(raw).hexdigest()
    install(ready, tree_entry(b'file', oid=bytes.fromhex(oid)))
    (ready / 'index').write_bytes(index_bytes(index_entry(b'file', oid=bytes.fromhex(oid))))
    path = ready / 'objects' / oid[:2] / oid[2:]
    path.parent.mkdir(exist_ok=True)
    if not missing:
        path.write_bytes(zlib.compress(raw))
        _, result = read_verified_project_objects(setup[0])
        assert result['objects'] == {oid: b'hello'}
    else:
        with pytest.raises(ProjectGitSourceError, match='project_git_blob_missing'):
            read_verified_project_objects(setup[0])
