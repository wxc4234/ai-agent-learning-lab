"""同一授权作用域的暂存差异观察；不比较工作树或提供执行能力。"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from app.services.workspace.git.index_v2 import MAX_INDEX_BYTES, IndexV2Error, parse_index_v2
from app.services.workspace.git.loose_commit import HeadObjectObservation
from app.services.workspace.git.project_config import ProjectGitConfigObservation
from app.services.workspace.git.project_head import ProjectGitHeadObservation, _observe_file
from app.services.workspace.git.project_index import ProjectGitIndexObservation
from app.services.workspace.git.project_layout import ProjectGitLayoutObservation
from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection
from app.services.workspace.git.staged_compare import StagedChange, StagedCompareError, compare_staged
from app.services.workspace.git.tree_graph import HeadTreeGraphObservation, _observe_graph


@dataclass(frozen=True, slots=True)
class ProjectStagedObservation:
    head: HeadTreeGraphObservation
    index: ProjectGitIndexObservation
    status: Literal['staged_compared', 'comparison_unavailable']
    # 缺失原因可同时存在；None不是无差异，只有比较成功才有tuple。
    unavailable_reasons: tuple[str, ...]
    changes: tuple[StagedChange, ...] | None


@contextmanager
def _observe_staged(root: int) -> Generator[tuple, None, None]:
    with _observe_graph(root) as graph:
        config, _, _, _, graph_status, leaves, _ = graph
        # 使用graph仍活跃的.git描述符，不调用已结束作用域的公开index读取。
        with _observe_file(config.descriptor, ('index',), missing_allowed=True, max_bytes=MAX_INDEX_BYTES) as raw:
            snapshot = None
            if raw is not None:
                try:
                    snapshot = parse_index_v2(raw)
                except IndexV2Error as exc:
                    raise ProjectGitSourceError(exc.code) from None
            reasons = []
            if leaves is None:
                reasons.append(graph_status)
            if snapshot is None:
                reasons.append('index_missing')
            changes = None
            if not reasons:
                try:
                    changes = compare_staged(leaves, snapshot)
                except StagedCompareError as exc:
                    raise ProjectGitSourceError(exc.code) from None
            # index和整个HEAD图的复核均在第二次授权后执行；异常不交付预计算差异。
            yield graph, snapshot, tuple(reasons), changes


def read_project_staged(request: object) -> ProjectStagedObservation:
    """两个短只读事务；所有文件检查退出成功后构造携带同一来源的结果。"""
    source, (graph, snapshot, reasons, changes) = _read_with_inspection(request, _observe_staged)
    config, head, commit_status, commit, graph_status, leaves, count = graph
    checked = ProjectGitConfigObservation(
        ProjectGitLayoutObservation(source, config.identity, config.count), config.digest, config.settings,
    )
    observed_head = HeadTreeGraphObservation(
        HeadObjectObservation(ProjectGitHeadObservation(checked, head), commit_status, commit),
        graph_status, leaves, count,
    )
    observed_index = ProjectGitIndexObservation(checked, 'index_missing' if snapshot is None else 'index_present', snapshot)
    return ProjectStagedObservation(observed_head, observed_index,
                                    'comparison_unavailable' if reasons else 'staged_compared', reasons, changes)
