"""packed-refs保守ASCII/SHA-1子集；完整有界解析，不运行Git或验证对象。

格式依据：https://github.com/git/git/blob/master/refs/packed-backend.c
支持无头部及peeled/fully-peeled/sorted特征；未知特征拒绝而不猜测。
"""

import re
from dataclasses import dataclass, replace

from app.services.workspace.git.project_source import ProjectGitSourceError

MAX_PACKED_BYTES = 256 * 1024
MAX_PACKED_REFS = 2000
MAX_PACKED_LINE_BYTES = 1100


@dataclass(frozen=True, slots=True)
class PackedReference:
    name: str
    object_id: str
    # peeled值仅为文件中的声明，不能替代原始ID或证明对象的类型/存在性。
    peeled_object_id: str | None = None


def _invalid(code: str = 'project_git_packed_unsupported') -> ProjectGitSourceError:
    return ProjectGitSourceError(code)


def _oid(value: str) -> str:
    if re.fullmatch(r'[0-9a-f]{40}', value) is None or value == '0' * 40:
        raise _invalid()
    return value


def parse_packed_refs(raw: bytes) -> tuple[PackedReference, ...]:
    """读取完成后才调用；缺失与空文件由调用方区分，解析失败无部分结果。"""
    if type(raw) is not bytes:
        raise _invalid()
    if len(raw) > MAX_PACKED_BYTES:
        raise _invalid('project_git_packed_limit')
    if not raw:
        return ()
    if not raw.endswith(b'\n'):
        raise _invalid('project_git_packed_incomplete')
    try:
        lines = raw[:-1].decode('ascii', errors='strict').split('\n')
    except UnicodeDecodeError:
        raise _invalid() from None
    records: list[PackedReference] = []
    seen: set[str] = set()
    sorted_declared = False
    for number, line in enumerate(lines):
        if len(line) > MAX_PACKED_LINE_BYTES:
            raise _invalid('project_git_packed_limit')
        if number == 0 and line.startswith('# pack-refs with: '):
            traits = line[len('# pack-refs with: '):].strip(' ').split(' ')
            traits = [trait for trait in traits if trait]
            if len(set(traits)) != len(traits) or set(traits) - {'peeled', 'fully-peeled', 'sorted'}:
                raise _invalid()
            sorted_declared = 'sorted' in traits
            continue
        if line.startswith('^'):
            if not records or records[-1].peeled_object_id is not None:
                raise _invalid()
            records[-1] = replace(records[-1], peeled_object_id=_oid(line[1:]))
            continue
        if len(records) >= MAX_PACKED_REFS:
            raise _invalid('project_git_packed_limit')
        parts = line.split(' ')
        if len(parts) != 2:
            raise _invalid()
        oid, name = parts
        components = name.split('/')
        if (not name.startswith('refs/') or len(components) < 3 or len(components) > 16
                or len(name) > 1024 or any(
                    re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]*', part) is None
                    or part.endswith(('.', '.lock')) or '..' in part for part in components)):
            raise _invalid()
        if name in seen or (sorted_declared and records and name <= records[-1].name):
            raise _invalid()
        seen.add(name)
        records.append(PackedReference(name, _oid(oid)))
    # 未声明sorted的合法顺序可以保留；检查同名和目录/引用前缀冲突不改变输出。
    if any('/'.join(name.split('/')[:index]) in seen
           for name in seen for index in range(2, len(name.split('/')))):
        raise _invalid()
    return tuple(records)
