"""HEAD/loose/packed branch引用的有界观察；不读取对象，不执行Git或跟随符号引用链。

规则依据：https://git-scm.com/docs/git-check-ref-format
首版仅支持ASCII分支路径、SHA-1非零小写ID、可选单个LF；packed-refs使用独立预算并完整校验。
"""

import os
import re
import stat
from collections.abc import Generator
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from functools import partial
from typing import Literal

from app.services.workspace.git.project_config import ProjectGitConfigObservation, _observe_config
from app.services.workspace.git.project_layout import ProjectGitLayoutObservation, _signature
from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection

from app.services.workspace.git.packed_refs import MAX_PACKED_BYTES, parse_packed_refs

MAX_REF_BYTES = 1024
MAX_REF_COMPONENTS = 16


def _error(code: str = 'project_git_head_unsupported') -> ProjectGitSourceError:
    return ProjectGitSourceError(code)


def _line(raw: bytes) -> str:
    if type(raw) is not bytes:
        raise _error()
    if len(raw) > MAX_REF_BYTES:
        raise _error('project_git_head_limit')
    try:
        return raw.removesuffix(b'\n').decode('ascii', errors='strict')
    except UnicodeDecodeError:
        raise _error() from None


def parse_object_id(raw: bytes) -> str:
    value = _line(raw)
    if re.fullmatch(r'[0-9a-f]{40}', value) is None or value == '0' * 40:
        raise _error()
    return value


def parse_head(raw: bytes) -> tuple[str | None, str | None]:
    """返回(branch_ref, object_id)，只验证支持的表示，不验证对象存在。"""
    line = _line(raw)
    if not line.startswith('ref: '):
        return None, parse_object_id(raw)
    name = line[5:]
    parts = name.split('/')
    if (not name.startswith('refs/heads/') or len(parts) > MAX_REF_COMPONENTS
            or any(re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]*', part) is None
                   or part.endswith(('.', '.lock')) or '..' in part for part in parts)):
        raise _error()
    return name, None


def _read(descriptor: int, *, limit: int = MAX_REF_BYTES) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    data = bytearray()
    while len(data) <= limit:
        chunk = os.read(descriptor, min(4096, limit + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
    raise _error('project_git_head_limit')


def _require_absent(parent: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise _error('project_git_head_changed')


@contextmanager
def _observe_file(directory: int, parts: tuple[str, ...], *, missing_allowed: bool = False, max_bytes: int = MAX_REF_BYTES) -> Generator[bytes | None, None, None]:
    """parts仅由固定HEAD名或已验证的refs/heads路径产生，不是外部路径接口。"""
    with ExitStack() as stack:
        parent = directory
        links = []
        absent = None
        for part in parts[:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                absent = (parent, part)
                break
            stack.callback(os.close, child)
            links.append((parent, part, child, _signature(os.fstat(child))))
            parent = child

        def verify_parents() -> None:
            for directory_fd, name, fd, signature in links:
                if (_signature(os.fstat(fd)) != signature
                        or _signature(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)) != signature):
                    raise _error('project_git_head_changed')

        info = None
        if absent is None:
            try:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                absent = (parent, parts[-1])
        if absent is not None:
            if not missing_allowed:
                raise _error('project_git_head_missing')
            verify_parents()
            _require_absent(*absent)
            yield None
            verify_parents()
            _require_absent(*absent)
            return
        if info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise _error()
        if info.st_size > max_bytes:
            raise _error('project_git_head_limit')
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        def verify() -> None:
            verify_parents()
            if (_signature(os.fstat(fd)) != _signature(info)
                    or _signature(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)) != _signature(info)):
                raise _error('project_git_head_changed')
        verify()
        read = _read if max_bytes == MAX_REF_BYTES else partial(_read, limit=max_bytes)
        raw = read(fd)
        verify()
        yield raw
        verify()
        if read(fd) != raw:
            raise _error('project_git_head_changed')
        verify()


@dataclass(frozen=True, slots=True)
class HeadReference:
    state: Literal['detached', 'branch', 'unborn_candidate']
    branch_ref: str | None
    object_id: str | None
    origin: Literal['head', 'loose', 'packed', 'missing']


@dataclass(frozen=True, slots=True)
class ProjectGitHeadObservation:
    config: ProjectGitConfigObservation
    head: HeadReference


@contextmanager
def _observe_head(root: int) -> Generator[tuple, None, None]:
    with _observe_config(root) as config:
        directory = config.descriptor
        # 两种来源一起覆盖授权复核；即便loose命中也不隐藏损坏的packed数据。
        with _observe_file(directory, ('HEAD',)) as raw, _observe_file(
            directory, ('packed-refs',), missing_allowed=True, max_bytes=MAX_PACKED_BYTES,
        ) as packed_raw:
            if raw is None:
                raise _error('project_git_head_missing')
            packed = parse_packed_refs(packed_raw) if packed_raw is not None else ()
            branch, detached = parse_head(raw)
            context = _observe_file(directory, tuple(branch.split('/')), missing_allowed=True) if branch else nullcontext(None)
            with context as target:
                if branch is None:
                    head = HeadReference('detached', None, detached, 'head')
                elif target is not None:
                    head = HeadReference('branch', branch, parse_object_id(target), 'loose')
                else:
                    match = next((record for record in packed if record.name == branch), None)
                    head = (HeadReference('branch', branch, match.object_id, 'packed') if match
                            else HeadReference('unborn_candidate', branch, None, 'missing'))
                yield config, head


def read_project_git_head(request: object) -> ProjectGitHeadObservation:
    source, (config, head) = _read_with_inspection(request, _observe_head)
    checked = ProjectGitConfigObservation(ProjectGitLayoutObservation(source, config.identity, config.count),
                                          config.digest, config.settings)
    return ProjectGitHeadObservation(checked, head)
