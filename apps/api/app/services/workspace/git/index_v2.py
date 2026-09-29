"""SHA-1 index v2的有界纯解析；不访问文件、不执行Git或确认工作树状态。

格式依据：https://git-scm.com/docs/index-format
首版拒绝所有扩展、assume-valid/extended标志和非UTF-8路径。
"""

from dataclasses import dataclass
from hashlib import sha1
from struct import Struct

MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_INDEX_ENTRIES = 2000
MAX_INDEX_PATH_BYTES = 4096
_HEADER = Struct('>4sII')
_ENTRY = Struct('>10I20sH')
_MODES = frozenset({0o100644, 0o100755, 0o120000, 0o160000})


class IndexV2Error(ValueError):
    """稳定分类，不回显原始索引、路径或底层异常。"""

    def __init__(self, code: str = 'index_v2_invalid') -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class IndexStat:
    # 索引缓存字段，不是本次文件系统观察；size只保存原格式的低32位。
    ctime_seconds: int
    ctime_nanoseconds: int
    mtime_seconds: int
    mtime_nanoseconds: int
    device: int
    inode: int
    uid: int
    gid: int
    size_low32: int


@dataclass(frozen=True, slots=True)
class IndexEntry:
    path: str
    stage: int
    mode: int
    object_id: str
    cached_stat: IndexStat


@dataclass(frozen=True, slots=True)
class IndexV2Snapshot:
    entries: tuple[IndexEntry, ...]
    byte_count: int
    checksum: str


def _path(raw: bytes) -> str:
    if not raw or len(raw) > MAX_INDEX_PATH_BYTES:
        raise IndexV2Error('index_v2_path_invalid')
    try:
        name = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise IndexV2Error('index_v2_encoding_unsupported') from None
    # 不规范化或strip，避免两个原始路径被合并。首版采用跨平台保守子集。
    if ('\\' in name or ':' in name or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or any(part in ('', '.', '..') or part.lower() == '.git' for part in name.split('/'))):
        raise IndexV2Error('index_v2_path_invalid')
    return name


def parse_index_v2(data: bytes) -> IndexV2Snapshot:
    """仅接收完整字节；空输入/截断/未知不能伪装成合法空索引。"""
    if type(data) is not bytes:
        raise IndexV2Error('index_v2_input_invalid')
    if len(data) > MAX_INDEX_BYTES:
        raise IndexV2Error('index_v2_limit')
    if len(data) < _HEADER.size + 20:
        raise IndexV2Error()
    magic, version, count = _HEADER.unpack_from(data)
    if magic != b'DIRC':
        raise IndexV2Error()
    if version != 2:
        raise IndexV2Error('index_v2_version_unsupported')
    if count > MAX_INDEX_ENTRIES:
        raise IndexV2Error('index_v2_limit')
    end = len(data) - 20
    if count > (end - _HEADER.size) // 64:
        raise IndexV2Error()
    digest = sha1(data[:end]).digest()
    if digest != data[end:]:
        raise IndexV2Error('index_v2_checksum_mismatch')
    offset = _HEADER.size
    entries: list[IndexEntry] = []
    previous: tuple[bytes, int] | None = None
    stages: dict[bytes, set[int]] = {}
    for _ in range(count):
        start = offset
        if offset + _ENTRY.size > end:
            raise IndexV2Error()
        fields = _ENTRY.unpack_from(data, offset)
        flags = fields[11]
        if flags & 0xc000:
            raise IndexV2Error('index_v2_flags_unsupported')
        if fields[6] not in _MODES or fields[1] >= 1_000_000_000 or fields[3] >= 1_000_000_000:
            raise IndexV2Error()
        if fields[10] == bytes(20):
            raise IndexV2Error('index_v2_object_id_unsupported')
        path_start = offset + _ENTRY.size
        nul = data.find(b'\0', path_start, min(end, path_start + MAX_INDEX_PATH_BYTES + 1))
        if nul < 0:
            raise IndexV2Error('index_v2_path_invalid')
        raw_path = data[path_start:nul]
        name = _path(raw_path)
        if flags & 0xfff != min(len(raw_path), 0xfff):
            raise IndexV2Error()
        # 对齐相对于每条entry起点；路径后必须有1到8个零字节，包含终止NUL。
        offset = start + ((_ENTRY.size + len(raw_path) + 8) // 8) * 8
        if offset > end or any(data[nul:offset]):
            raise IndexV2Error()
        stage = (flags >> 12) & 3
        key = (raw_path, stage)
        if previous is not None and key <= previous:
            raise IndexV2Error('index_v2_order_invalid')
        previous = key
        existing = stages.setdefault(raw_path, set())
        if existing and (stage == 0 or 0 in existing):
            raise IndexV2Error('index_v2_stage_conflict')
        existing.add(stage)
        cached = IndexStat(
            ctime_seconds=fields[0], ctime_nanoseconds=fields[1],
            mtime_seconds=fields[2], mtime_nanoseconds=fields[3],
            device=fields[4], inode=fields[5], uid=fields[7], gid=fields[8], size_low32=fields[9],
        )
        entries.append(IndexEntry(name, stage, fields[6], fields[10].hex(), cached))
    if offset != end:
        # 包括Git允许忽略的可选扩展，本课也不静默跳过；不能误读split/sparse语义。
        raise IndexV2Error('index_v2_extensions_unsupported')
    for name, present in stages.items():
        parts = name.split(b'/')
        for length in range(1, len(parts)):
            if present & stages.get(b'/'.join(parts[:length]), set()):
                raise IndexV2Error('index_v2_stage_conflict')
    return IndexV2Snapshot(tuple(entries), len(data), digest.hex())
