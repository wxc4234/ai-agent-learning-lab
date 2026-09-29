"""同一次授权观察HEAD根tree；来源支持loose/自包含pack，不递归或执行Git。"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from app.services.workspace.git.loose_commit import HeadObjectObservation, _observe_commit
from app.services.workspace.git.loose_tree import MAX_COMPRESSED_BYTES, LooseTree, LooseTreeError, parse_loose_tree
from app.services.workspace.git.project_config import ProjectGitConfigObservation
from app.services.workspace.git.project_head import ProjectGitHeadObservation
from app.services.workspace.git.project_layout import ProjectGitLayoutObservation
from app.services.workspace.git.object_store import observe_object
from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection


@dataclass(frozen=True, slots=True)
class HeadTreeObservation:
    commit: HeadObjectObservation
    # 前置对象未取得与根tree缺失分别保留，不将任何缺失补成空树。
    status: Literal['tree_verified', 'tree_object_missing',
                    'commit_object_missing', 'no_head_object_observed']
    tree: LooseTree | None


@contextmanager
def _observe_tree(root: int) -> Generator[tuple, None, None]:
    with _observe_commit(root) as (config, head, commit_status, commit):
        if commit is None:
            status = ('no_head_object_observed' if commit_status == 'no_head_object_observed'
                      else 'commit_object_missing')
            yield config, head, commit_status, commit, status, None
            return
        # ID来自本次已校验commit；所有上游描述符在tree退出复核前保持存活。
        oid = commit.tree_id
        with observe_object(config, oid, max_bytes=MAX_COMPRESSED_BYTES) as raw:
            tree = None
            if raw is not None:
                try:
                    tree = parse_loose_tree(raw, expected_oid=oid)
                except LooseTreeError as exc:
                    raise ProjectGitSourceError(exc.code) from None
            status = 'tree_object_missing' if tree is None else 'tree_verified'
            yield config, head, commit_status, commit, status, tree


def read_head_loose_tree(request: object) -> HeadTreeObservation:
    """文件I/O在两个短只读事务之外；所有退出检查通过后才返回聚合结果。"""
    source, (config, head, commit_status, commit, status, tree) = _read_with_inspection(request, _observe_tree)
    checked = ProjectGitConfigObservation(
        ProjectGitLayoutObservation(source, config.identity, config.count), config.digest, config.settings,
    )
    observed_commit = HeadObjectObservation(ProjectGitHeadObservation(checked, head), commit_status, commit)
    return HeadTreeObservation(observed_commit, status, tree)
