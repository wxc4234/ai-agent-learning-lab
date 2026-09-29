"""有界观察HEAD tree图；支持loose/自包含pack，仅目录递归，叶对象不读取。"""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Literal

from app.services.workspace.git.loose_commit import HeadObjectObservation, _observe_commit
from app.services.workspace.git.loose_tree import MAX_COMPRESSED_BYTES, LooseTree, LooseTreeError, parse_loose_tree
from app.services.workspace.git.project_config import ProjectGitConfigObservation
from app.services.workspace.git.project_head import ProjectGitHeadObservation
from app.services.workspace.git.project_layout import ProjectGitLayoutObservation
from app.services.workspace.git.object_store import observe_object
from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection

# 含根tree的唯一对象预算也限制存活描述符：每对象最多两个目录和一个文件。
MAX_TREES = 64
MAX_COMPRESSED_TOTAL = 2 * 1024 * 1024
MAX_EXPANDED_TOTAL = 4 * 1024 * 1024
MAX_DEPTH = 16
MAX_VISITS = 4000
MAX_LEAVES = 2000
MAX_PATH_BYTES = 4096
MAX_PATH_TOTAL = 1024 * 1024


@dataclass(frozen=True, slots=True)
class TreeLeaf:
    path: str
    mode: int
    object_id: str


@dataclass(frozen=True, slots=True)
class HeadTreeGraphObservation:
    commit: HeadObjectObservation
    status: Literal['tree_graph_observed', 'commit_object_missing', 'no_head_object_observed']
    # None表示前置对象未取得；空tuple仅表示已完整观察且没有叶条目。
    leaves: tuple[TreeLeaf, ...] | None
    unique_trees: int


def _limit() -> ProjectGitSourceError:
    return ProjectGitSourceError('project_git_tree_graph_limit')


@contextmanager
def _observe_graph(root: int) -> Generator[tuple, None, None]:
    with _observe_commit(root) as (config, head, commit_status, commit), ExitStack() as stack:
        if commit is None:
            status = ('no_head_object_observed' if commit_status == 'no_head_object_observed'
                      else 'commit_object_missing')
            yield config, head, commit_status, commit, status, None, 0
            return
        cache: dict[str, LooseTree] = {}
        leaves: list[TreeLeaf] = []
        compressed_total = expanded_total = visits = path_total = 0

        def read(oid: str) -> LooseTree:
            nonlocal compressed_total, expanded_total
            if oid in cache:
                return cache[oid]
            if len(cache) >= MAX_TREES or compressed_total >= MAX_COMPRESSED_TOTAL:
                raise _limit()
            # 全部唯一对象的观察器留在ExitStack内，第二次授权后统一退出复核。
            raw = stack.enter_context(observe_object(
                config, oid,
                max_bytes=min(MAX_COMPRESSED_BYTES, MAX_COMPRESSED_TOTAL - compressed_total),
            ))
            if raw is None:
                raise ProjectGitSourceError('project_git_tree_graph_object_missing')
            compressed_total += len(raw)
            try:
                tree = parse_loose_tree(raw, expected_oid=oid)
            except LooseTreeError as exc:
                raise ProjectGitSourceError(exc.code) from None
            # 检查后才缓存；单次解析另受1MiB上限约束，瞬时最多多一个对象。
            expanded_total += tree.body_bytes + len(f'tree {tree.body_bytes}'.encode()) + 1
            if expanded_total > MAX_EXPANDED_TOTAL:
                raise _limit()
            cache[oid] = tree
            return tree

        def walk(oid: str, prefix: str, depth: int, ancestors: frozenset[str]) -> None:
            nonlocal visits, path_total
            if depth > MAX_DEPTH:
                raise _limit()
            # 共享子树合法；仅祖先回边是循环，不能把全局去重集合当循环检测。
            if oid in ancestors:
                raise ProjectGitSourceError('project_git_tree_graph_cycle')
            tree = read(oid)
            lineage = ancestors | {oid}
            for item in tree.entries:
                visits += 1
                path = prefix + item.name
                size = len(path.encode('utf-8'))
                path_total += size
                if visits > MAX_VISITS or size > MAX_PATH_BYTES or path_total > MAX_PATH_TOTAL:
                    raise _limit()
                if item.mode == 0o40000:
                    walk(item.object_id, path + '/', depth + 1, lineage)
                else:
                    if len(leaves) >= MAX_LEAVES:
                        raise _limit()
                    leaves.append(TreeLeaf(path, item.mode, item.object_id))

        walk(commit.tree_id, '', 0, frozenset())
        # Git目录排序保证展开顺序；共享对象节省I/O，但每次路径展开仍计入预算。
        yield config, head, commit_status, commit, 'tree_graph_observed', tuple(leaves), len(cache)


def read_head_tree_graph(request: object) -> HeadTreeGraphObservation:
    """两个短只读事务包围全部文件I/O；任一退出检查失败均不交付部分结果。"""
    source, (config, head, commit_status, commit, status, leaves, count) = _read_with_inspection(request, _observe_graph)
    checked = ProjectGitConfigObservation(
        ProjectGitLayoutObservation(source, config.identity, config.count), config.digest, config.settings,
    )
    observed = HeadObjectObservation(ProjectGitHeadObservation(checked, head), commit_status, commit)
    return HeadTreeGraphObservation(observed, status, leaves, count)
