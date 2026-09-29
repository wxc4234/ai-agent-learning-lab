"""有界Git元数据布局观察，不解析配置/对象正文，不证明仓库可执行。

布局依据：https://git-scm.com/docs/gitrepository-layout
首版只接受普通.git目录；工作树指针和共享对象来源需要另行设计。
"""

import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from app.services.workspace.git.project_source import (
    ProjectGitSourceError, ProjectGitSourceObservation, _read_with_inspection,
)

MAX_LAYOUT_ENTRIES = 4096
MAX_LAYOUT_DEPTH = 16
_INDIRECT = frozenset({'commondir', 'gitdir', 'config.worktree', 'worktrees', 'modules',
    'objects/info/alternates', 'objects/info/http-alternates'})


@dataclass(frozen=True, slots=True)
class ProjectGitLayoutObservation:
    source: ProjectGitSourceObservation
    git_identity: tuple[int, int]
    entry_count: int
    scope: Literal['metadata_layout_only'] = 'metadata_layout_only'


def _signature(info: os.stat_result) -> tuple[int, ...]:
    # 包含对象身份和变更时间，仍不等于内容摘要或原子快照。
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _scan(directory: int) -> dict[str, tuple[int, ...]]:
    records: dict[str, tuple[int, ...]] = {}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def walk(parent: int, prefix: str, depth: int) -> None:
        if depth > MAX_LAYOUT_DEPTH:
            raise ProjectGitSourceError('project_git_layout_limit')
        # scandir迭代消费，禁止先完整列举/排序；深度限制同时限制活跃描述符数量。
        with os.scandir(parent) as entries:
            for entry in entries:
                if len(records) >= MAX_LAYOUT_ENTRIES:
                    raise ProjectGitSourceError('project_git_layout_limit')
                name = prefix + entry.name
                if name in _INDIRECT:
                    raise ProjectGitSourceError('project_git_layout_unsupported')
                info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise ProjectGitSourceError('project_git_layout_unsupported')
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ProjectGitSourceError('project_git_layout_unsupported')
                records[name] = _signature(info)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, flags, dir_fd=parent)
                    try:
                        if _signature(os.fstat(child)) != records[name]:
                            raise ProjectGitSourceError('project_git_layout_changed')
                        walk(child, name + '/', depth + 1)
                        if (_signature(os.fstat(child)) != records[name]
                                or _signature(os.stat(entry.name, dir_fd=parent, follow_symlinks=False)) != records[name]):
                            raise ProjectGitSourceError('project_git_layout_changed')
                    finally:
                        os.close(child)

    walk(directory, '', 0)
    for name, directory_required in [('HEAD', False), ('config', False), ('objects', True), ('refs', True)]:
        record = records.get(name)
        if record is None or (stat.S_ISDIR(record[2]) if directory_required else stat.S_ISREG(record[2])) is False:
            raise ProjectGitSourceError('project_git_layout_unsupported')
    return records


@contextmanager
def _observe_layout(root: int) -> Generator['_OpenedLayout', None, None]:
    if os.scandir not in os.supports_fd:
        raise ProjectGitSourceError('project_git_source_platform_unsupported')
    try:
        named = os.stat('.git', dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        raise ProjectGitSourceError('project_git_layout_missing') from None
    if not stat.S_ISDIR(named.st_mode):
        raise ProjectGitSourceError('project_git_layout_unsupported')
    descriptor = os.open('.git', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    try:
        before = _signature(named)
        def verify() -> None:
            if (_signature(os.fstat(descriptor)) != before
                    or _signature(os.stat('.git', dir_fd=root, follow_symlinks=False)) != before):
                raise ProjectGitSourceError('project_git_layout_changed')
        verify()
        records = _scan(descriptor)
        verify()
        yield _OpenedLayout((named.st_dev, named.st_ino), len(records), descriptor)
        # 第二次数据库授权已关闭；仍持有同一个.git描述符，再比较完整有界布局。
        if _scan(descriptor) != records:
            raise ProjectGitSourceError('project_git_layout_changed')
        verify()
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _OpenedLayout:
    identity: tuple[int, int]
    count: int
    descriptor: int = field(repr=False)


def read_project_git_layout(request: object) -> ProjectGitLayoutObservation:
    """只通过资源引用进入本次授权作用域；不接受旧观察、路径或裸描述符。"""
    source, opened = _read_with_inspection(request, _observe_layout)
    return ProjectGitLayoutObservation(source, opened.identity, opened.count)
