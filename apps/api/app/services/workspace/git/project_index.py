"""授权作用域内观察index v2；不比较工作树，也不授予Git执行权。"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from app.services.workspace.git.index_v2 import MAX_INDEX_BYTES, IndexV2Error, IndexV2Snapshot, parse_index_v2
from app.services.workspace.git.project_config import ProjectGitConfigObservation, _OpenedConfig, _observe_config
from app.services.workspace.git.project_head import _observe_file
from app.services.workspace.git.project_layout import ProjectGitLayoutObservation
from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection


@dataclass(frozen=True, slots=True)
class ProjectGitIndexObservation:
    config: ProjectGitConfigObservation
    # 缺失不是空暂存区；只有完整合法的空index才返回空entries。
    status: Literal['index_present', 'index_missing']
    index: IndexV2Snapshot | None


@contextmanager
def _observe_index(root: int) -> Generator[tuple[_OpenedConfig, IndexV2Snapshot | None], None, None]:
    with _observe_config(root) as config, _observe_file(
        config.descriptor, ('index',), missing_allowed=True, max_bytes=MAX_INDEX_BYTES,
    ) as raw:
        snapshot = None
        if raw is not None:
            try:
                snapshot = parse_index_v2(raw)
            except IndexV2Error as exc:
                # 仅保留解析器固定错误码，不回显路径、内容或底层异常。
                raise ProjectGitSourceError(exc.code) from None
        # 描述符保持存活直到第二次授权完成；退出时复核身份、字节及布局。
        yield config, snapshot


def read_project_git_index(request: object) -> ProjectGitIndexObservation:
    """两个短只读事务包围文件观察；全部退出检查通过后才交付结果。"""
    source, (config, snapshot) = _read_with_inspection(request, _observe_index)
    checked = ProjectGitConfigObservation(
        ProjectGitLayoutObservation(source, config.identity, config.count), config.digest, config.settings,
    )
    return ProjectGitIndexObservation(checked, 'index_missing' if snapshot is None else 'index_present', snapshot)
