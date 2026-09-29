"""Git配置的保守子集审查；不是完整Git解析器，不运行配置或授予执行权。

语法/配置依据：https://git-scm.com/docs/git-config
首版只支持单个core节、明确等号赋值及整行注释；合法但未支持的Git语法也拒绝。
"""

import os
import re
import stat
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

from app.services.workspace.git.project_layout import ProjectGitLayoutObservation, _observe_layout, _signature
from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection

from app.services.workspace.git.object_store import GitObjectStore

MAX_CONFIG_BYTES = 16 * 1024
MAX_CONFIG_LINES = 128
MAX_CONFIG_LINE_BYTES = 1024
_BOOLEAN_KEYS = frozenset({'filemode', 'logallrefupdates', 'ignorecase', 'precomposeunicode', 'symlinks'})


def _reject(code: str = 'project_git_config_unsupported') -> ProjectGitSourceError:
    return ProjectGitSourceError(code)


def parse_project_git_config(raw: bytes) -> tuple[tuple[str, str], ...]:
    """固定白名单输出，无原始配置/路径/外部程序回显；未知语义一律拒绝。"""
    if type(raw) is not bytes:
        raise _reject()
    if len(raw) > MAX_CONFIG_BYTES:
        raise _reject('project_git_config_limit')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise _reject() from None
    # 首版采用LF；BOM、CRLF、续行、引号与行尾注释均不作猜测性兼容。
    if any((ord(char) < 32 and char not in '\n\t') or ord(char) == 127 for char in text):
        raise _reject()
    lines = text.split('\n')
    if len(lines) > MAX_CONFIG_LINES:
        raise _reject('project_git_config_limit')
    values: dict[str, str] = {}
    section_seen = False
    for line in lines:
        if len(line.encode('utf-8')) > MAX_CONFIG_LINE_BYTES:
            raise _reject('project_git_config_limit')
        stripped = line.strip(' \t')
        if not stripped or stripped.startswith(('#', ';')):
            continue
        if stripped.lower() == '[core]':
            if section_seen:
                raise _reject()
            section_seen = True
            continue
        match = re.fullmatch(r'([a-zA-Z][a-zA-Z0-9]*)[ \t]*=[ \t]*([a-zA-Z0-9]+)', stripped)
        if not section_seen or match is None:
            raise _reject()
        key, value = (part.lower() for part in match.groups())
        if key in values:
            raise _reject()
        allowed = ((key in _BOOLEAN_KEYS and value in {'true', 'false'})
            or (key == 'repositoryformatversion' and value == '0')
            or (key in {'bare', 'autocrlf'} and value == 'false'))
        if not allowed:
            raise _reject()
        values[key] = value
    if values.get('repositoryformatversion') != '0' or values.get('bare') != 'false':
        raise _reject()
    return tuple(sorted(values.items()))


@dataclass(frozen=True, slots=True)
class ProjectGitConfigObservation:
    layout: ProjectGitLayoutObservation
    config_sha256: str
    settings: tuple[tuple[str, str], ...]
    scope: Literal['core_config_subset_only'] = 'core_config_subset_only'


def _read_bounded(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    data = bytearray()
    while len(data) <= MAX_CONFIG_BYTES:
        chunk = os.read(descriptor, min(4096, MAX_CONFIG_BYTES + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
    raise _reject('project_git_config_limit')


@dataclass(frozen=True, slots=True)
class _OpenedConfig:
    identity: tuple[int, int]
    count: int
    digest: str
    settings: tuple[tuple[str, str], ...]
    descriptor: int = field(repr=False)
    object_store: GitObjectStore = field(repr=False)


@contextmanager
def _observe_config(root: int) -> Generator[_OpenedConfig, None, None]:
    # 布局描述符不能在上一次观察结束后重新使用；与配置描述符一起覆盖授权复核。
    with _observe_layout(root) as opened:
        directory = opened.descriptor
        info = os.stat('config', dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise _reject()
        if info.st_size > MAX_CONFIG_BYTES:
            raise _reject('project_git_config_limit')
        descriptor = os.open('config', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            def verify() -> None:
                if (_signature(os.fstat(descriptor)) != _signature(info)
                        or _signature(os.stat('config', dir_fd=directory, follow_symlinks=False)) != _signature(info)):
                    raise _reject('project_git_config_changed')
            verify()
            raw = _read_bounded(descriptor)
            verify()
            settings = parse_project_git_config(raw)
            with ExitStack() as objects:
                yield _OpenedConfig(opened.identity, opened.count, sha256(raw).hexdigest(), settings, directory,
                                    GitObjectStore(directory, objects))
            # 第二次授权事务已关闭；内容和元数据都复核，不将相同inode当成未修改。
            verify()
            if _read_bounded(descriptor) != raw:
                raise _reject('project_git_config_changed')
            verify()
        finally:
            os.close(descriptor)


def read_project_git_config(request: object) -> ProjectGitConfigObservation:
    source, opened = _read_with_inspection(request, _observe_config)
    return ProjectGitConfigObservation(ProjectGitLayoutObservation(source, opened.identity, opened.count),
                                       opened.digest, opened.settings)
