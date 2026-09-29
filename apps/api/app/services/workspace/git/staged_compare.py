"""HEAD叶投影与index的纯比较；不读取项目、不推断重命名或证明快照同源。"""

import re
from dataclasses import dataclass
from typing import Literal

from app.services.workspace.git.index_v2 import IndexEntry, IndexV2Error, IndexV2Snapshot, _path
from app.services.workspace.git.tree_graph import TreeLeaf

MAX_ENTRIES = 2000  # 两侧分别限制；输出最多4000个不同路径。
MAX_PATH_BYTES = 4096
MAX_PATH_TOTAL = 1024 * 1024
_MODES = frozenset({0o100644, 0o100755, 0o120000, 0o160000})


class StagedCompareError(ValueError):
    def __init__(self, code: str = 'staged_compare_input_invalid') -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ObjectVersion:
    mode: int
    object_id: str


@dataclass(frozen=True, slots=True)
class IndexVersion:
    stage: int
    version: ObjectVersion


@dataclass(frozen=True, slots=True)
class StagedChange:
    path: str
    status: Literal['added', 'deleted', 'modified', 'unmerged']
    head: ObjectVersion | None
    # 删除时为空；冲突只保留实际存在的stage，不虚构缺失的某一侧。
    index: tuple[IndexVersion, ...]


def compare_staged(head: object, index: object) -> tuple[StagedChange, ...]:
    """完整检查比较相关字段后返回；缓存stat和摘要不是内容相等或授权的依据。"""
    if type(head) is not tuple or type(index) is not IndexV2Snapshot or type(index.entries) is not tuple:
        raise StagedCompareError()
    if len(head) > MAX_ENTRIES or len(index.entries) > MAX_ENTRIES:
        raise StagedCompareError('staged_compare_limit')
    path_total = 0

    def checked(path: str, mode: int, oid: str) -> bytes:
        nonlocal path_total
        if type(path) is not str or len(path) > MAX_PATH_BYTES:
            raise StagedCompareError()
        try:
            raw = path.encode('utf-8', errors='strict')
            _path(raw)
        except (UnicodeError, IndexV2Error):
            raise StagedCompareError() from None
        path_total += len(raw)
        if len(raw) > MAX_PATH_BYTES or path_total > MAX_PATH_TOTAL:
            raise StagedCompareError('staged_compare_limit')
        if (type(mode) is not int or mode not in _MODES or type(oid) is not str
                or re.fullmatch(r'[0-9a-f]{40}', oid) is None or oid == '0' * 40):
            raise StagedCompareError()
        return raw

    def no_prefix_conflict(stages: dict[bytes, set[int]]) -> None:
        for name, present in stages.items():
            parts = name.split(b'/')
            for length in range(1, len(parts)):
                if present & stages.get(b'/'.join(parts[:length]), set()):
                    raise StagedCompareError()

    before: dict[bytes, ObjectVersion] = {}
    previous = b''
    for leaf in head:
        if type(leaf) is not TreeLeaf:
            raise StagedCompareError()
        name = checked(leaf.path, leaf.mode, leaf.object_id)
        if name <= previous:
            raise StagedCompareError()
        previous = name
        before[name] = ObjectVersion(leaf.mode, leaf.object_id)
    no_prefix_conflict({name: {0} for name in before})
    after: dict[bytes, list[IndexVersion]] = {}
    stages: dict[bytes, set[int]] = {}
    last: tuple[bytes, int] | None = None
    for record in index.entries:
        if type(record) is not IndexEntry or type(record.stage) is not int or record.stage not in range(4):
            raise StagedCompareError()
        name = checked(record.path, record.mode, record.object_id)
        key = (name, record.stage)
        if last is not None and key <= last:
            raise StagedCompareError()
        last = key
        present = stages.setdefault(name, set())
        if present and (record.stage == 0 or 0 in present):
            raise StagedCompareError()
        present.add(record.stage)
        after.setdefault(name, []).append(IndexVersion(record.stage, ObjectVersion(record.mode, record.object_id)))
    no_prefix_conflict(stages)
    changes = []
    for name in sorted(before.keys() | after.keys()):
        old = before.get(name)
        versions = tuple(after.get(name, ()))
        status: Literal['added', 'deleted', 'modified', 'unmerged']
        if versions and versions[0].stage != 0:
            status = 'unmerged'
        elif not versions:
            status = 'deleted'
        elif old is None:
            status = 'added'
        elif old != versions[0].version:
            status = 'modified'
        else:
            continue
        changes.append(StagedChange(name.decode('utf-8'), status, old, versions))
    return tuple(changes)
