"""隔离PG和临时对象目录的图观察验收；不执行Git。"""

import os
from dataclasses import replace

import pytest
from sqlalchemy import event

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import project_head as head, project_source as source, tree_graph as graph
from tests.workspace.git.test_loose_tree import encoded, entry
from tests.workspace.git.test_project_tree import install, ready, repository, setup, target

__all__ = ['ready', 'repository', 'setup', 'target']


def put(ready, body):
    raw, oid, _ = encoded(body)
    path = ready / 'objects' / oid[:2] / oid[2:]
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    return oid, path


@pytest.fixture
def trees(ready):
    oid, child = put(ready, entry(b'file') + entry(b'link', b'120000') + entry(b'sub', b'160000'))
    empty, _ = put(ready, b'')
    root, commit = install(ready, entry(b'a', b'40000', bytes.fromhex(oid))
                           + entry(b'b', b'40000', bytes.fromhex(oid))
                           + entry(b'empty', b'40000', bytes.fromhex(empty)))
    return root, child, commit


def test_shared_subtree_empty_directory_and_leaves_only(setup, trees, engine, monkeypatch):
    original = head._read
    fds, inodes, statements = [], [], []
    def checked(fd, **kw):
        assert all(s.closed and not s.in_transaction() for s in setup[2])
        fds.append(fd)
        inodes.append(os.fstat(fd).st_ino)
        return original(fd, **kw)
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    monkeypatch.setattr(head, '_read', checked)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        result = graph.read_head_tree_graph(setup[0])
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert result.status == 'tree_graph_observed' and result.unique_trees == 3
    assert result.leaves is not None
    assert [x.path for x in result.leaves] == ['a/file', 'a/link', 'a/sub', 'b/file', 'b/link', 'b/sub']
    assert inodes.count(trees[1].stat().st_ino) == 2  # 共享对象只初读和复读一次。
    assert len(statements) == 2 and all(s.lower().startswith('select') for s in statements)
    for fd in set(fds):
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('kind', ['empty', 'head', 'commit'])
def test_empty_and_unavailable_are_distinct(setup, ready, kind):
    _, commit = install(ready, b'')
    if kind == 'head':
        (ready / 'refs/heads/main').unlink()
    elif kind == 'commit':
        commit.unlink()
    result = graph.read_head_tree_graph(setup[0])
    assert result.leaves == (() if kind == 'empty' else None)
    assert result.status == {'empty': 'tree_graph_observed', 'head': 'no_head_object_observed',
                             'commit': 'commit_object_missing'}[kind]


@pytest.mark.parametrize('budget,exact', [('MAX_TREES', 3), ('MAX_VISITS', 9), ('MAX_LEAVES', 6),
    ('MAX_DEPTH', 1), ('MAX_PATH_BYTES', 6), ('MAX_PATH_TOTAL', 41)])
def test_exact_and_exceeded_structural_budgets(setup, trees, monkeypatch, budget, exact):
    monkeypatch.setattr(graph, budget, exact)
    assert graph.read_head_tree_graph(setup[0]).leaves is not None
    monkeypatch.setattr(graph, budget, exact - 1)
    with pytest.raises(source.ProjectGitSourceError, match='tree_graph_limit'):
        graph.read_head_tree_graph(setup[0])


@pytest.mark.parametrize('budget', ['MAX_COMPRESSED_TOTAL', 'MAX_EXPANDED_TOTAL'])
def test_byte_budgets_include_root_and_shared_objects_once(setup, ready, trees, monkeypatch, budget):
    import zlib
    paths = [trees[0], trees[1]]
    _, empty = put(ready, b'')
    paths.append(empty)
    exact = sum(len(p.read_bytes()) if budget == 'MAX_COMPRESSED_TOTAL' else len(zlib.decompress(p.read_bytes())) for p in paths)
    monkeypatch.setattr(graph, budget, exact)
    assert graph.read_head_tree_graph(setup[0]).unique_trees == 3
    monkeypatch.setattr(graph, budget, exact - 1)
    with pytest.raises(source.ProjectGitSourceError):
        graph.read_head_tree_graph(setup[0])


@pytest.mark.parametrize('kind', ['missing', 'corrupt', 'link'])
def test_bad_child_never_returns_partial_leaves(setup, trees, kind):
    child = trees[1]
    child.unlink()
    if kind == 'corrupt':
        child.write_bytes(b'bad')
    elif kind == 'link':
        child.symlink_to(trees[0])
    with pytest.raises(source.ProjectGitSourceError):
        graph.read_head_tree_graph(setup[0])


@pytest.mark.parametrize('kind', ['replace', 'modify', 'delete', 'root', 'commit', 'revision'])
def test_changes_after_traversal_reject(setup, trees, monkeypatch, kind):
    original = source.read_owned_git_binding
    calls = 0
    def changed(*args, **kw):
        nonlocal calls
        calls += 1
        row = dict(original(*args, **kw))
        if calls == 2:
            child = trees[1]
            if kind == 'replace':
                new = child.with_name('new')
                new.write_bytes(child.read_bytes())
                new.replace(child)
            elif kind == 'delete':
                child.unlink()
            elif kind == 'revision':
                row['binding_revision'] += 1
            else:
                {'modify': child, 'root': trees[0], 'commit': trees[2]}[kind].write_bytes(b'changed')
        return row
    monkeypatch.setattr(source, 'read_owned_git_binding', changed)
    with pytest.raises(source.ProjectGitSourceError):
        graph.read_head_tree_graph(setup[0])


@pytest.mark.parametrize('kind', ['permission', 'interrupt', 'second-read'])
def test_failures_release_held_files(setup, trees, monkeypatch, kind):
    original = head._read
    fds = []
    calls = 0
    def failed(fd, **kw):
        nonlocal calls
        fds.append(fd)
        if os.fstat(fd).st_ino == trees[1].stat().st_ino:
            calls += 1
            if kind == 'interrupt':
                raise KeyboardInterrupt()
            if kind == 'permission' or calls == 2:
                raise PermissionError('private path')
        return original(fd, **kw)
    monkeypatch.setattr(head, '_read', failed)
    with pytest.raises(KeyboardInterrupt if kind == 'interrupt' else source.ProjectGitSourceError):
        graph.read_head_tree_graph(setup[0])
    for fd in set(fds):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_cycle_guard_with_injected_parser_result(setup, trees, monkeypatch):
    # 合法哈希对象难以构造祖先循环；此处只对防御分支注入结果，不宣称真实循环对象验收。
    original = graph.parse_loose_tree
    def cyclic(raw, *, expected_oid):
        result = original(raw, expected_oid=expected_oid)
        return replace(result, entries=(replace(result.entries[0], mode=0o40000, object_id=expected_oid),))
    monkeypatch.setattr(graph, 'parse_loose_tree', cyclic)
    with pytest.raises(source.ProjectGitSourceError, match='tree_graph_cycle'):
        graph.read_head_tree_graph(setup[0])


@pytest.mark.parametrize('kind', ['foreign', 'stale'])
def test_authorization_precedes_traversal(setup, target, monkeypatch, kind):
    request = dict(setup[0])
    request.update({'user_id': target['other_id']} if kind == 'foreign' else {'binding_revision': 2})
    monkeypatch.setattr(graph, '_observe_graph', lambda *a: pytest.fail('authorize first'))
    with pytest.raises(WorkspaceNotAccessibleError if kind == 'foreign' else source.ProjectGitSourceError):
        graph.read_head_tree_graph(request)


def test_multilevel_paths(setup, ready):
    oid, _ = put(ready, entry(b'leaf'))
    oid, _ = put(ready, entry(b'inner', b'40000', bytes.fromhex(oid)))
    install(ready, entry(b'outer', b'40000', bytes.fromhex(oid)))
    result = graph.read_head_tree_graph(setup[0])
    assert result.leaves is not None and [x.path for x in result.leaves] == ['outer/inner/leaf']


@pytest.mark.parametrize('kind', ['missing', 'corrupt'])
def test_later_child_failure_discards_previously_collected_leaf(setup, ready, kind):
    oid, path = put(ready, entry())
    install(ready, entry(b'first') + entry(b'later', b'40000', bytes.fromhex(oid)))
    if kind == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'bad')
    with pytest.raises(source.ProjectGitSourceError):
        graph.read_head_tree_graph(setup[0])
