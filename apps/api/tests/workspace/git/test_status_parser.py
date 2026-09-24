"""固定协议样例测试，无Git进程、文件系统访问或数据库。"""

from dataclasses import FrozenInstanceError

import pytest

from app.services.workspace.git import status_parser as parser


def parse(data):
    return parser.parse_git_status(data, source_truncated=False)


@pytest.mark.parametrize('xy', [' M', ' T', ' A', ' D', 'M ', 'MM', 'MT', 'MD', 'T ', 'TM', 'TT', 'TD', 'A ', 'AM', 'AT', 'AD', 'D '])
def test_index_and_worktree_columns(xy):
    entry, = parse(xy.encode() + b' file\0').entries
    assert entry.kind == 'tracked' and entry.xy == xy
    assert entry.index_status == xy[0] and entry.worktree_status == xy[1]
    assert entry.path == 'file' and entry.original_path is None


@pytest.mark.parametrize('xy', ['R ', 'RM', 'RT', 'RD', 'C ', 'CM', 'CT', 'CD', ' R', ' C'])
def test_destination_then_source(xy):
    result = parse(xy.encode() + b' new name\0old\nname\0?? next\0')
    assert result.entries[0].path == 'new name'
    assert result.entries[0].original_path == 'old\nname'
    assert result.entries[1].path == 'next'
    assert len(result.entries) == 2


@pytest.mark.parametrize('xy', ['DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU', '??', '!!'])
def test_nonordinary_status_not_mislabelled_as_staging(xy):
    entry, = parse(xy.encode() + b' name\0').entries
    assert entry.kind == {'??': 'untracked', '!!': 'ignored'}.get(xy, 'unmerged')
    assert entry.index_status is None and entry.worktree_status is None


@pytest.mark.parametrize('name', [' leading ', 'a\nb\rc\td', '中文😀', 'a -> b', '"quoted"', r'back\slash', '--option', 'folder/'])
def test_path_is_not_split_trimmed_or_unescaped(name):
    entry, = parse(b'?? ' + name.encode() + b'\0').entries
    assert entry.path == name


@pytest.mark.parametrize('data', [b'\0', b'M file\0', b'MMfile\0', b' M \0', b' M file', b' M file\0\0',
    b'R  target\0', b'R  target\0\0', b'R  target\0source', b'?? a\0bad\0',
    b'## main\0', b'1 .M x\0', b'  unchanged\0', b'ZZ file\0', b'M? file\0', b' m submodule\0', b'RR path\0old\0', b'\xffM file\0'])
def test_malformed_stream_rejected_as_whole(data):
    with pytest.raises(parser.GitStatusParseError) as caught:
        parse(data)
    assert caught.value.code == 'git_status_invalid_format'


@pytest.mark.parametrize('data', [b'?? \xffPRIVATE\0', b'R  new\0\xed\xa0\x80PRIVATE\0'])
def test_invalid_utf8_never_replaced_or_leaked(data):
    with pytest.raises(parser.GitStatusParseError) as caught:
        parse(data)
    assert caught.value.code == 'git_status_invalid_encoding'
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('data', [b'', b'?? full\0', b'R  target\0', b'\xff'])
def test_explicit_truncation_never_returns_empty_or_prefix(data):
    with pytest.raises(parser.GitStatusParseError) as caught:
        parser.parse_git_status(data, source_truncated=True)
    assert caught.value.code == 'git_status_truncated'


@pytest.mark.parametrize('data,flag', [('?? x', False), (bytearray(), False), (None, False), (b'', 0), (b'', None)])
def test_invalid_input_types(data, flag):
    with pytest.raises(parser.GitStatusParseError) as caught:
        parser.parse_git_status(data, source_truncated=flag)
    assert caught.value.code == 'git_status_invalid_input'


def test_empty_output_and_immutable_snapshot():
    result = parse(b'')
    assert result.entries == () and result.byte_count == 0
    with pytest.raises(FrozenInstanceError):
        result.byte_count = 1
    with pytest.raises(FrozenInstanceError):
        parse(b'?? x\0').entries[0].path = 'changed'


def test_path_budget_uses_bytes_for_both_paths():
    path = ('😀' * (parser.MAX_STATUS_PATH_BYTES // 4)).encode()
    assert parse(b'?? ' + path + b'\0').entries[0].path.encode() == path
    for data in [b'?? ' + path + b'x\0', b'R  new\0' + path + b'x\0']:
        with pytest.raises(parser.GitStatusParseError) as caught:
            parse(data)
        assert caught.value.code == 'git_status_limit_exceeded'


def test_entry_limit_counts_renames_as_one():
    data = b'R  new\0old\0' * parser.MAX_STATUS_ENTRIES
    assert len(parse(data).entries) == parser.MAX_STATUS_ENTRIES
    with pytest.raises(parser.GitStatusParseError) as caught:
        parse(data + b'?? extra\0')
    assert caught.value.code == 'git_status_limit_exceeded'


def test_total_byte_limit_exact_and_over():
    # 实际硬上限，不通过调小常量替代边界测试。
    record = b'?? ' + b'x' * 4092 + b'\0'
    exact = record * 64
    assert len(exact) == parser.MAX_STATUS_BYTES
    assert parse(exact).byte_count == len(exact)
    with pytest.raises(parser.GitStatusParseError) as caught:
        parse(exact + b'\0')
    assert caught.value.code == 'git_status_limit_exceeded'
