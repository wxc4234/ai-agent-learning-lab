"""纯内存比较专项；快照由现有解析器构造，不运行Git或数据库。"""

from dataclasses import FrozenInstanceError, replace

import pytest

from app.services.workspace.git import staged_compare as compare
from app.services.workspace.git.index_v2 import parse_index_v2
from app.services.workspace.git.tree_graph import TreeLeaf
from tests.workspace.git.test_index_v2 import entry, fixture

OID = '12' * 20
OTHER = '34' * 20


def snapshot(*records):
    return parse_index_v2(fixture(*records))


def leaf(path='file.txt', mode=0o100644, oid=OID):
    return TreeLeaf(path, mode, oid)


def test_empty_and_equal_are_no_changes():
    assert compare.compare_staged((), snapshot()) == ()
    assert compare.compare_staged((leaf(),), snapshot(entry())) == ()


@pytest.mark.parametrize('kind', ['add', 'delete', 'content', 'mode', 'both', 'link', 'gitlink'])
def test_changes(kind):
    old = () if kind == 'add' else (leaf(),)
    kwargs = {}
    if kind in ('content', 'both'):
        kwargs['oid'] = bytes.fromhex(OTHER)
    if kind in ('mode', 'both', 'link', 'gitlink'):
        kwargs['mode'] = {'link': 0o120000, 'gitlink': 0o160000}.get(kind, 0o100755)
    new = snapshot() if kind == 'delete' else snapshot(entry(**kwargs))
    result, = compare.compare_staged(old, new)
    assert result.status == {'add': 'added', 'delete': 'deleted'}.get(kind, 'modified')
    assert result.head == (None if kind == 'add' else compare.ObjectVersion(0o100644, OID))
    assert bool(result.index) == (kind != 'delete')
    if result.index:
        assert result.index[0].version.object_id == (OTHER if kind in ('content', 'both') else OID)
    pytest.raises(FrozenInstanceError, setattr, result, 'path', 'other')


@pytest.mark.parametrize('stages', [(1,), (2,), (3,), (1, 2), (1, 3), (2, 3), (1, 2, 3)])
@pytest.mark.parametrize('has_head', [True, False])
def test_unmerged_preserves_only_observed_stages(stages, has_head):
    result, = compare.compare_staged((leaf(),) if has_head else (), snapshot(*(entry(stage=s) for s in stages)))
    assert result.status == 'unmerged'
    assert tuple(v.stage for v in result.index) == stages


@pytest.mark.parametrize('reverse', [False, True])
def test_file_directory_replacement(reverse):
    old, new = (leaf('a'),), snapshot(entry(b'a/b'))
    if reverse:
        old, new = (leaf('a/b'),), snapshot(entry(b'a'))
    assert [(c.path, c.status) for c in compare.compare_staged(old, new)] == [
        ('a', 'added' if reverse else 'deleted'), ('a/b', 'deleted' if reverse else 'added')]


def test_cross_stage_directory_conflict_and_raw_path_order():
    new = snapshot(entry(b'A'), entry(b'a', stage=2), entry(b'a/b', stage=3), entry('中'.encode()))
    result = compare.compare_staged((), new)
    assert [x.path for x in result] == ['A', 'a', 'a/b', '中']
    assert [x.status for x in result] == ['added', 'unmerged', 'unmerged', 'added']


def test_cached_stat_and_snapshot_metadata_are_not_comparison_evidence():
    new = snapshot(entry())
    changed = replace(new.entries[0], cached_stat=replace(new.entries[0].cached_stat, size_low32=123))
    assert compare.compare_staged((leaf(),), replace(new, entries=(changed,))) == ()


@pytest.mark.parametrize('old', [None, [], (None,), (leaf('b'), leaf('a')), (leaf(), leaf()),
    (leaf('a'), leaf('a/b')), (leaf('../a'),), (leaf('.git/a'),), (leaf('a\\b'),),
    (leaf('\ud800'),), (leaf('', oid=OID),), (leaf(mode=True),), (leaf(mode=0o40000),),
    (leaf(oid='0' * 40),), (leaf(oid='A' * 40),)])
def test_invalid_head(old):
    with pytest.raises(compare.StagedCompareError):
        compare.compare_staged(old, snapshot())


@pytest.mark.parametrize('kind', ['missing', 'list', 'record', 'stage', 'bool-stage', 'mixed', 'duplicate',
                                  'reverse', 'prefix', 'mode', 'oid', 'path'])
def test_invalid_index(kind):
    new = snapshot(entry())
    record = new.entries[0]
    if kind == 'missing':
        new = None
    elif kind == 'list':
        new = replace(new, entries=[])
    elif kind == 'record':
        new = replace(new, entries=(None,))
    elif kind in ('stage', 'bool-stage', 'mode', 'oid', 'path'):
        field, value = {'stage': ('stage', 4), 'bool-stage': ('stage', True), 'mode': ('mode', 0),
                        'oid': ('object_id', 'bad'), 'path': ('path', '/outside')}[kind]
        new = replace(new, entries=(replace(record, **{field: value}),))
    elif kind == 'mixed':
        new = replace(new, entries=(record, replace(record, stage=1)))
    elif kind == 'duplicate':
        new = replace(new, entries=(record, record))
    elif kind == 'reverse':
        new = replace(new, entries=(replace(record, path='b'), replace(record, path='a')))
    else:
        new = replace(new, entries=(replace(record, path='a'), replace(record, path='a/b')))
    pytest.raises(compare.StagedCompareError, compare.compare_staged, (), new)


@pytest.mark.parametrize('budget,exact', [('MAX_ENTRIES', 1), ('MAX_PATH_BYTES', 8), ('MAX_PATH_TOTAL', 16)])
def test_exact_budget_and_overflow(monkeypatch, budget, exact):
    monkeypatch.setattr(compare, budget, exact)
    assert compare.compare_staged((leaf(),), snapshot(entry())) == ()
    monkeypatch.setattr(compare, budget, exact - 1)
    with pytest.raises(compare.StagedCompareError):
        compare.compare_staged((leaf(),), snapshot(entry()))


def test_no_rename_inference():
    result = compare.compare_staged((leaf('old'),), snapshot(entry(b'new')))
    assert [(x.path, x.status) for x in result] == [('new', 'added'), ('old', 'deleted')]
