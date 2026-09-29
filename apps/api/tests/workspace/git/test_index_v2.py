"""独立构造网络字节序index样本；不使用Git、数据库或项目文件。"""

from dataclasses import FrozenInstanceError
from hashlib import sha1
from struct import pack

import pytest

from app.services.workspace.git import index_v2 as index


def entry(path=b'file.txt', *, stage=0, mode=0o100644, flags=None, oid=b'\x12' * 20,
          ctime_ns=3, mtime_ns=5):
    stat_fields = (2, ctime_ns, 4, mtime_ns, 6, 7, mode, 8, 9, 10)
    if flags is None:
        flags = (stage << 12) | min(len(path), 4095)
    fixed = pack('!10I', *stat_fields) + oid + pack('!H', flags)
    return fixed + path + bytes(8 - ((len(fixed) + len(path)) % 8))


def signed(body):
    return body + sha1(body).digest()


def fixture(*entries, version=2, count=None, extra=b''):
    return signed(b'DIRC' + pack('!II', version, len(entries) if count is None else count) + b''.join(entries) + extra)


def test_empty_index_is_not_empty_input():
    result = index.parse_index_v2(fixture())
    assert result.entries == () and result.byte_count == 32
    with pytest.raises(index.IndexV2Error):
        index.parse_index_v2(b'')


@pytest.mark.parametrize('length', range(1, 17))
def test_entry_alignment_and_cached_fields(length):
    result = index.parse_index_v2(fixture(entry(b'a' * length), entry(b'z')))
    first = result.entries[0]
    assert first.path == 'a' * length
    assert first.stage == 0 and first.object_id == '12' * 20
    assert first.cached_stat == index.IndexStat(2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert result.entries[1].path == 'z'
    pytest.raises(FrozenInstanceError, setattr, first, 'stage', 2)


def test_unicode_spaces_and_raw_byte_order():
    names = ['Z', 'a/b c', 'é', '中文']
    result = index.parse_index_v2(fixture(*(entry(name.encode()) for name in names)))
    assert [item.path for item in result.entries] == names


@pytest.mark.parametrize('mode', [0o100644, 0o100755, 0o120000, 0o160000])
def test_supported_modes_are_data_not_execution(mode):
    assert index.parse_index_v2(fixture(entry(mode=mode))).entries[0].mode == mode


@pytest.mark.parametrize('stages', [(1,), (2,), (3,), (1, 2), (2, 3), (1, 2, 3)])
def test_partial_and_full_merge_stages(stages):
    result = index.parse_index_v2(fixture(*(entry(stage=stage) for stage in stages)))
    assert tuple(item.stage for item in result.entries) == stages


def test_cross_stage_directory_file_conflict_is_representable():
    result = index.parse_index_v2(fixture(entry(b'a', stage=1), entry(b'a/file', stage=2)))
    assert len(result.entries) == 2


@pytest.mark.parametrize('entries', [
    (entry(b'z'), entry(b'a')), (entry(), entry()), (entry(stage=2), entry(stage=1)),
    (entry(stage=1), entry(stage=1)), (entry(stage=0), entry(stage=2)),
    (entry(b'a'), entry(b'a/file')), (entry(b'a', stage=2), entry(b'a/file', stage=2)),
])
def test_order_duplicates_and_stage_conflicts(entries):
    with pytest.raises(index.IndexV2Error):
        index.parse_index_v2(fixture(*entries))


@pytest.mark.parametrize('path', [b'', b'/absolute', b'../file', b'a/../file', b'a/./file', b'a//file',
    b'trailing/', b'.git/config', b'.GIT/config', b'a/.git/x', b'a\\b', b'C:drive', b'control\n', b'\xff'])
def test_invalid_or_unsupported_paths(path):
    with pytest.raises(index.IndexV2Error):
        index.parse_index_v2(fixture(entry(path)))


@pytest.mark.parametrize('length', [4094, 4095, 4096])
def test_long_path_length_sentinel(length):
    result = index.parse_index_v2(fixture(entry(b'a' * length)))
    assert len(result.entries[0].path) == length


@pytest.mark.parametrize('change', ['flags-assume', 'flags-extended', 'flags-both', 'name-length',
    'sentinel-short', 'zero-oid', 'mode', 'ctime', 'mtime', 'padding', 'terminator', 'path-budget'])
def test_invalid_fixed_fields_and_padding(change):
    values = {
        'flags-assume': {'flags': 0x8008}, 'flags-extended': {'flags': 0x4008},
        'flags-both': {'flags': 0xc008}, 'name-length': {'flags': 7},
        'sentinel-short': {'flags': 0xfff}, 'zero-oid': {'oid': bytes(20)},
        'mode': {'mode': 0o040000}, 'ctime': {'ctime_ns': 1_000_000_000},
        'mtime': {'mtime_ns': 1_000_000_000}, 'path-budget': {'path': b'x' * 4097},
    }
    raw = entry(**values.get(change, {}))
    if change == 'padding':
        raw = raw[:-1] + b'X'
    elif change == 'terminator':
        raw = raw[:70] + b'X' * (len(raw) - 70)
    with pytest.raises(index.IndexV2Error):
        index.parse_index_v2(fixture(raw))


@pytest.mark.parametrize('version', [0, 1, 3, 4, 0xffffffff])
def test_other_versions_rejected(version):
    with pytest.raises(index.IndexV2Error) as caught:
        index.parse_index_v2(fixture(version=version))
    assert caught.value.code == 'index_v2_version_unsupported'


@pytest.mark.parametrize('extra', [b'TREE' + pack('!I', 0), b'link' + pack('!I', 20) + bytes(20),
    b'sdir' + pack('!I', 0), b'UNTR' + pack('!I', 0), b'garbage'])
def test_extensions_never_silently_skipped(extra):
    with pytest.raises(index.IndexV2Error) as caught:
        index.parse_index_v2(fixture(entry(), extra=extra))
    assert caught.value.code == 'index_v2_extensions_unsupported'


def test_corruption_and_inconsistent_counts():
    raw = fixture(entry())
    corruptions = [b'X' + raw[1:], raw[:-1] + bytes([raw[-1] ^ 1]),
                   fixture(entry(), count=0), fixture(entry(), count=2), fixture(count=0xffffffff)]
    for corrupt in corruptions:
        with pytest.raises(index.IndexV2Error):
            index.parse_index_v2(corrupt)
    for length in range(len(raw)):
        with pytest.raises(index.IndexV2Error):
            index.parse_index_v2(raw[:length])


def test_exact_byte_and_entry_budgets(monkeypatch):
    raw = fixture(entry(b'a'), entry(b'b'))
    monkeypatch.setattr(index, 'MAX_INDEX_BYTES', len(raw))
    monkeypatch.setattr(index, 'MAX_INDEX_ENTRIES', 2)
    assert len(index.parse_index_v2(raw).entries) == 2
    with pytest.raises(index.IndexV2Error) as caught:
        index.parse_index_v2(raw + b'x')
    assert caught.value.code == 'index_v2_limit'
    monkeypatch.setattr(index, 'MAX_INDEX_ENTRIES', 1)
    with pytest.raises(index.IndexV2Error) as caught:
        index.parse_index_v2(raw)
    assert caught.value.code == 'index_v2_limit'


@pytest.mark.parametrize('value', [None, '', bytearray(), memoryview(b'')])
def test_wrong_input_type_is_not_empty_index(value):
    with pytest.raises(index.IndexV2Error) as caught:
        index.parse_index_v2(value)
    assert caught.value.code == 'index_v2_input_invalid'
