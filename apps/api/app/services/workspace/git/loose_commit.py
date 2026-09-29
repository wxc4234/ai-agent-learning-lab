"""单个HEAD commit的有界校验；来源支持loose/自包含pack，不执行Git。

对象封装依据：https://git-scm.com/book/en/v2/Git-Internals-Git-Objects
首版正文仅支持tree、最多16个parent、author、committer与UTF-8消息。
"""

import re
import zlib
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha1
from typing import Literal

from app.services.workspace.git.project_config import ProjectGitConfigObservation
from app.services.workspace.git.project_head import ProjectGitHeadObservation, _observe_head
from app.services.workspace.git.project_layout import ProjectGitLayoutObservation
from app.services.workspace.git.object_store import observe_object
from app.services.workspace.git.project_source import ProjectGitSourceError, _read_with_inspection

MAX_COMPRESSED_BYTES = 256 * 1024
MAX_OBJECT_BYTES = 1024 * 1024
MAX_COMMIT_HEADER_BYTES = 16 * 1024
MAX_COMMIT_PARENTS = 16


def _error(code: str = 'project_git_commit_unsupported') -> ProjectGitSourceError:
    return ProjectGitSourceError(code)


def _oid(value: str) -> str:
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{40}', value) is None or value == '0' * 40:
        raise _error()
    return value


@dataclass(frozen=True, slots=True)
class LooseCommit:
    object_id: str
    body_bytes: int
    tree_id: str
    parent_ids: tuple[str, ...]
    message_bytes: int


def parse_loose_commit(compressed: bytes, *, expected_oid: str) -> LooseCommit:
    """完整通过后才返回投影；不回显提交正文、身份或底层压缩错误。"""
    _oid(expected_oid)
    if type(compressed) is not bytes:
        raise _error()
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise _error('project_git_commit_limit')
    decoder = zlib.decompressobj()
    try:
        # 多读一个哨兵字节识别溢出；不调用无界decompress或flush。
        raw = decoder.decompress(compressed, MAX_OBJECT_BYTES + 1)
    except zlib.error:
        raise _error('project_git_commit_compression_invalid') from None
    if len(raw) > MAX_OBJECT_BYTES or decoder.unconsumed_tail:
        raise _error('project_git_commit_limit')
    if not decoder.eof or decoder.unused_data:
        raise _error('project_git_commit_compression_invalid')
    header, separator, body = raw.partition(b'\0')
    if not separator or len(header) > 64 or re.fullmatch(rb'commit (0|[1-9][0-9]{0,9})', header) is None:
        raise _error()
    if int(header[7:]) != len(body):
        raise _error('project_git_commit_size_mismatch')
    # SHA-1在此只是Git对象命名规则，不是签名验证或授权依据。
    if sha1(raw).hexdigest() != expected_oid:
        raise _error('project_git_commit_hash_mismatch')
    headers, blank, message = body.partition(b'\n\n')
    if not blank or len(headers) > MAX_COMMIT_HEADER_BYTES or b'\0' in body:
        raise _error()
    try:
        body.decode('utf-8', errors='strict')
        lines = headers.decode('utf-8', errors='strict').split('\n')
    except UnicodeDecodeError:
        raise _error() from None
    if not lines or not lines[0].startswith('tree '):
        raise _error()
    tree = _oid(lines[0][5:])
    parents = []
    index = 1
    while index < len(lines) and lines[index].startswith('parent '):
        if len(parents) >= MAX_COMMIT_PARENTS:
            raise _error('project_git_commit_limit')
        parents.append(_oid(lines[index][7:]))
        index += 1
    if len(set(parents)) != len(parents) or len(lines) != index + 2:
        raise _error()
    # 保守身份语法，不将名字/邮箱/时间戳带入返回值；扩展头需另行审查。
    identity = r'[^\x00-\x1f\x7f<>]+ <[^\x00-\x20\x7f<>]+> (0|[1-9][0-9]{0,11}) [+-]([01][0-9]|2[0-3])[0-5][0-9]'
    for line, key in zip(lines[index:], ('author', 'committer')):
        if re.fullmatch(key + ' ' + identity, line) is None:
            raise _error()
    return LooseCommit(expected_oid, len(body), tree, tuple(parents), len(message))


@dataclass(frozen=True, slots=True)
class HeadObjectObservation:
    head: ProjectGitHeadObservation
    status: Literal['commit_verified', 'commit_object_missing', 'no_head_object_observed']
    commit: LooseCommit | None


@contextmanager
def _observe_commit(root: int) -> Generator[tuple, None, None]:
    with _observe_head(root) as (config, head):
        if head.object_id is None:
            # 仅保留原始未诞生候选，不创建虚构的零ID对象或空提交。
            yield config, head, 'no_head_object_observed', None
            return
        oid = _oid(head.object_id)
        # 目录来自本次活跃作用域；路径仅由已验证ID分片，不接受外部路径。
        with observe_object(config, oid, max_bytes=MAX_COMPRESSED_BYTES) as raw:
            if raw is None:
                yield config, head, 'commit_object_missing', None
            else:
                commit = parse_loose_commit(raw, expected_oid=oid)
                yield config, head, 'commit_verified', commit


def read_head_loose_commit(request: object) -> HeadObjectObservation:
    source, (config, head, status, commit) = _read_with_inspection(request, _observe_commit)
    checked_config = ProjectGitConfigObservation(ProjectGitLayoutObservation(source, config.identity, config.count),
                                                 config.digest, config.settings)
    return HeadObjectObservation(ProjectGitHeadObservation(checked_config, head), status, commit)
