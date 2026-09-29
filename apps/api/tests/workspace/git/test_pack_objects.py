"""pack格式纯解析与临时Git交叉验证，不操作项目Git状态。"""
import os
import subprocess
import zlib
from hashlib import sha1
from struct import pack

import pytest

from app.services.workspace.git import pack_objects as module


def header(kind, size):
    value = bytearray([(kind << 4) | (size & 15)])
    size >>= 4
    while size:
        value[-1] |= 128
        value.append(size & 127)
        size >>= 7
    return bytes(value)


def build(*records):
    raw = b'PACK' + pack('>II', 2, len(records)) + b''.join(records)
    return raw + sha1(raw).digest()


def test_base_and_deltas():
    base = b'old\n'
    oid = sha1(b'blob 4\0' + base).digest()
    first = header(3, 4) + zlib.compress(base)
    delta = b'\x04\x04\x04new\n'
    for second in (header(7, len(delta)) + oid + zlib.compress(delta),
                   header(6, len(delta)) + bytes([len(first)]) + zlib.compress(delta)):
        objects = module.parse_pack(build(first, second))
        assert [o.body for o in objects] == [base, b'new\n']
    # REF_DELTA也允许引用同一pack后面的完整对象。
    assert module.parse_pack(build(header(7, len(delta)) + oid + zlib.compress(delta), first))[0].body == b'new\n'


@pytest.mark.parametrize('delta', [b'', b'\x03\x01\x01x', b'\x04\x01\x00', b'\x04\x02\x03abc', b'\x04\x01\x91\x05\x01'])
def test_delta_failure(delta):
    with pytest.raises(module.PackError):
        module.apply_delta(b'base', delta)


def test_copy_instruction():
    assert module.apply_delta(b'abcdef', b'\x06\x04\x91\x01\x03\x01!') == b'bcd!'
    assert module.apply_delta(b'x' * 65536, b'\x80\x80\x04\x80\x80\x04\x80') == b'x' * 65536


@pytest.mark.parametrize('kind', ['checksum', 'count', 'type', 'truncated', 'missing-base', 'trailing', 'bomb'])
def test_pack_failures(kind):
    record = header(3, 4) + zlib.compress(b'base')
    raw = build(record)
    if kind == 'checksum':
        raw = raw[:-1] + bytes([raw[-1] ^ 1])
    elif kind == 'count':
        raw = build(record, b'')
    elif kind == 'type':
        raw = build(header(5, 4) + zlib.compress(b'base'))
    elif kind == 'truncated':
        raw = build(record[:-2])
    elif kind == 'missing-base':
        raw = build(header(7, 3) + b'\x11' * 20 + zlib.compress(b'\x00\x00\x00'))
    elif kind == 'trailing':
        raw = build(record + b'x')
    else:
        raw = build(header(3, 4) + zlib.compress(b'x' * 10000))
    with pytest.raises(module.PackError):
        module.parse_pack(raw)


@pytest.mark.parametrize('name,limit', [('MAX_OBJECTS', 1), ('MAX_OBJECT_BYTES', 4), ('MAX_TOTAL_BYTES', 4)])
def test_exact_limits(monkeypatch, name, limit):
    raw = build(header(3, 4) + zlib.compress(b'base'))
    monkeypatch.setattr(module, name, limit)
    assert module.parse_pack(raw)[0].body == b'base'
    monkeypatch.setattr(module, name, limit - 1)
    with pytest.raises(module.PackError):
        module.parse_pack(raw)


def test_real_git_pack(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.test',
           'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.test'}
    def git(*args):
        return subprocess.run(['/usr/bin/git', '-C', str(root), *args], env=env, check=True, capture_output=True).stdout
    git('init', '-q')
    for n in range(3):
        (root / 'file').write_text('same content\n' * 100 + str(n))
        git('add', 'file')
        git('commit', '-qm', f'change {n}')
    git('repack', '-adf')
    packed, = (root / '.git/objects/pack').glob('*.pack')
    objects = module.parse_pack(packed.read_bytes())
    for obj in objects:
        assert git('cat-file', obj.kind, obj.object_id) == obj.body
    assert len(objects) == 9
    assert os.path.isdir(root)
