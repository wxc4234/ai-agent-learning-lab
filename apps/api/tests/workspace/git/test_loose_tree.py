"""合成二进制对象测试，不执行Git、不使用数据库或读取项目文件。"""

import zlib
from dataclasses import FrozenInstanceError
from hashlib import sha1

import pytest

from app.services.workspace.git import loose_tree as tree


def entry(name=b'file', mode=b'100644', oid=bytes(range(20))):
    return mode + b' ' + name + b'\0' + oid


def encoded(body=b'', header=None):
    raw = (header if header is not None else f'tree {len(body)}'.encode()) + b'\0' + body
    return zlib.compress(raw), sha1(raw).hexdigest(), raw


def parse(body):
    compressed, oid, _ = encoded(body)
    return tree.parse_loose_tree(compressed, expected_oid=oid)


def test_known_empty_tree_and_immutable_projection():
    compressed, oid, _ = encoded()
    assert oid == '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
    result = tree.parse_loose_tree(compressed, expected_oid=oid)
    assert result.entries == () and result.body_bytes == 0
    pytest.raises(FrozenInstanceError, setattr, result, 'body_bytes', 1)


@pytest.mark.parametrize('mode', [b'40000', b'100644', b'100755', b'120000', b'160000'])
def test_supported_modes_and_binary_object_id(mode):
    result = parse(entry('中文 name'.encode(), mode))
    record, = result.entries
    assert record.name == '中文 name' and record.mode == int(mode, 8)
    assert record.object_id == bytes(range(20)).hex()  # 包含NUL/空格分隔之外的二进制内容。
    pytest.raises(FrozenInstanceError, setattr, record, 'name', 'other')


def test_tree_sort_uses_directory_slash_and_original_bytes():
    body = entry(b'a.c') + entry(b'a', b'40000') + entry(b'a0') + entry('中'.encode())
    assert [item.name for item in parse(body).entries] == ['a.c', 'a', 'a0', '中']


@pytest.mark.parametrize('body', [
    entry(b'b') + entry(b'a'), entry(b'a', b'40000') + entry(b'a.c'),
    entry() + entry(), entry(b'a') + entry(b'a.c') + entry(b'a', b'40000'),
])
def test_bad_sort_and_nonadjacent_duplicate_names(body):
    with pytest.raises(tree.LooseTreeError, match='loose_tree_order_invalid'):
        parse(body)


@pytest.mark.parametrize('name', [b'', b'.', b'..', b'.git', b'.GiT', b'a/b', b'a\\b', b'a:b', b'a\n', b'\x7f'])
def test_unsupported_names(name):
    with pytest.raises(tree.LooseTreeError, match='loose_tree_name_unsupported'):
        parse(entry(name))


@pytest.mark.parametrize('mode', [b'040000', b'100600', b'100664', b'0', b'777777', b'-40000', b' 40000'])
def test_noncanonical_modes(mode):
    with pytest.raises(tree.LooseTreeError, match='loose_tree_mode_unsupported'):
        parse(entry(mode=mode))


@pytest.mark.parametrize('body', [entry(b'\xff'), entry(oid=b'\0' * 20), entry() + b'x', b'100644 file'])
def test_invalid_encoding_id_or_trailing_entry(body):
    with pytest.raises(tree.LooseTreeError):
        parse(body)


@pytest.mark.parametrize('length', range(1, len(entry())))
def test_every_entry_truncation(length):
    with pytest.raises(tree.LooseTreeError):
        parse(entry()[:length])


@pytest.mark.parametrize('header', [b'blob 0', b'tree 00', b'tree -1', b'tree +0', b'tree 1', b'tree', b'tree  0', b'tree ' + b'9' * 40])
def test_invalid_object_headers(header):
    compressed, oid, _ = encoded(header=header)
    with pytest.raises(tree.LooseTreeError):
        tree.parse_loose_tree(compressed, expected_oid=oid)


@pytest.mark.parametrize('kind', ['bad-zlib', 'truncated', 'trailing', 'concatenated', 'hash', 'bomb'])
def test_envelope_failures(kind):
    compressed, oid, _ = encoded(entry())
    if kind == 'bad-zlib':
        compressed = b'not zlib'
    elif kind == 'truncated':
        compressed = compressed[:-1]
    elif kind == 'trailing':
        compressed += b'x'
    elif kind == 'concatenated':
        compressed += zlib.compress(b'extra')
    elif kind == 'hash':
        oid = 'f' * 40
    else:
        compressed, oid, _ = encoded(b'x' * tree.MAX_OBJECT_BYTES)
    with pytest.raises(tree.LooseTreeError):
        tree.parse_loose_tree(compressed, expected_oid=oid)


@pytest.mark.parametrize('value', [None, '', bytearray(b'x'), 12])
def test_input_bytes_type(value):
    with pytest.raises(tree.LooseTreeError, match='loose_tree_input_invalid'):
        tree.parse_loose_tree(value, expected_oid='a' * 40)


@pytest.mark.parametrize('oid', [None, 12, '', 'A' * 40, '0' * 40, 'a' * 39, 'a' * 64, '../private'])
def test_expected_oid(oid):
    with pytest.raises(tree.LooseTreeError, match='loose_tree_input_invalid'):
        tree.parse_loose_tree(encoded()[0], expected_oid=oid)


@pytest.mark.parametrize('budget', ['MAX_COMPRESSED_BYTES', 'MAX_OBJECT_BYTES', 'MAX_TREE_ENTRIES', 'MAX_NAME_BYTES'])
def test_exact_budget_and_one_over(monkeypatch, budget):
    body = entry(b'name')
    compressed, oid, raw = encoded(body)
    limit = {'MAX_COMPRESSED_BYTES': len(compressed), 'MAX_OBJECT_BYTES': len(raw),
             'MAX_TREE_ENTRIES': 1, 'MAX_NAME_BYTES': 4}[budget]
    monkeypatch.setattr(tree, budget, limit)
    assert len(tree.parse_loose_tree(compressed, expected_oid=oid).entries) == 1
    monkeypatch.setattr(tree, budget, limit - 1)
    with pytest.raises(tree.LooseTreeError):
        tree.parse_loose_tree(compressed, expected_oid=oid)


def test_default_entry_and_name_budgets():
    body = b''.join(entry(f'{i:04}'.encode()) for i in range(tree.MAX_TREE_ENTRIES))
    assert len(parse(body).entries) == tree.MAX_TREE_ENTRIES
    with pytest.raises(tree.LooseTreeError, match='loose_tree_limit'):
        parse(body + entry(b'zzzz'))
    assert parse(entry(b'x' * tree.MAX_NAME_BYTES)).entries[0].name == 'x' * tree.MAX_NAME_BYTES
    with pytest.raises(tree.LooseTreeError):
        parse(entry(b'x' * (tree.MAX_NAME_BYTES + 1)))
