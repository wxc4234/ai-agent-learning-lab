"""SHA-1自包含pack v2/v3纯解码；预算内完整验证后交付对象。

格式依据：https://git-scm.com/docs/gitformat-pack
不读取idx、不执行Git；外部基对象的thin pack不作为完整磁盘对象库接受。
"""
import zlib
from dataclasses import dataclass
from hashlib import sha1
from struct import unpack_from

MAX_PACK_BYTES = 16 * 1024 * 1024
MAX_OBJECT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_OBJECTS = 2000
MAX_DELTA_DEPTH = 32


class PackError(ValueError):
    def __init__(self, code='git_pack_invalid'):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PackedObject:
    kind: str
    object_id: str
    body: bytes


def apply_delta(base: bytes, delta: bytes) -> bytes:
    cursor = 0
    def byte():
        nonlocal cursor
        if cursor >= len(delta):
            raise PackError()
        value = delta[cursor]
        cursor += 1
        return value
    def size():
        value = 0
        for shift in range(0, 35, 7):
            current = byte()
            value |= (current & 127) << shift
            if not current & 128:
                return value
        raise PackError('git_pack_limit')
    if size() != len(base):
        raise PackError()
    target = size()
    if target > MAX_OBJECT_BYTES:
        raise PackError('git_pack_limit')
    output = bytearray()
    while cursor < len(delta):
        opcode = byte()
        if opcode & 128:
            offset = length = 0
            for i in range(4):
                if opcode & (1 << i):
                    offset |= byte() << (8 * i)
            for i in range(3):
                if opcode & (16 << i):
                    length |= byte() << (8 * i)
            length = length or 65536
            if offset + length > len(base) or len(output) + length > target:
                raise PackError()
            output.extend(base[offset:offset + length])
        elif opcode:
            if cursor + opcode > len(delta) or len(output) + opcode > target:
                raise PackError()
            output.extend(delta[cursor:cursor + opcode])
            cursor += opcode
        else:
            raise PackError()
    if len(output) != target:
        raise PackError()
    return bytes(output)


def parse_pack(data: bytes) -> tuple[PackedObject, ...]:
    if type(data) is not bytes or len(data) < 32:
        raise PackError()
    if len(data) > MAX_PACK_BYTES:
        raise PackError('git_pack_limit')
    magic, version, count = unpack_from('>4sII', data)
    if magic != b'PACK' or version not in (2, 3):
        raise PackError()
    if count > MAX_OBJECTS:
        raise PackError('git_pack_limit')
    if sha1(data[:-20]).digest() != data[-20:]:
        raise PackError('git_pack_checksum')
    cursor, total = 12, 0
    records = []
    def byte():
        nonlocal cursor
        if cursor >= len(data) - 20:
            raise PackError()
        value = data[cursor]
        cursor += 1
        return value
    for _ in range(count):
        offset = cursor
        first = byte()
        kind, size, shift = (first >> 4) & 7, first & 15, 4
        current = first
        while current & 128:
            if shift > 32:
                raise PackError('git_pack_limit')
            current = byte()
            size |= (current & 127) << shift
            shift += 7
        if size > MAX_OBJECT_BYTES:
            raise PackError('git_pack_limit')
        base = None
        if kind == 6:
            current = byte()
            distance = current & 127
            steps = 0
            while current & 128:
                steps += 1
                if steps > 5:
                    raise PackError()
                current = byte()
                distance = ((distance + 1) << 7) | (current & 127)
            if distance <= 0 or offset - distance < 12:
                raise PackError()
            base = offset - distance
        elif kind == 7:
            base = bytes(byte() for _ in range(20)).hex()
        elif kind not in (1, 2, 3, 4):
            raise PackError()
        decoder = zlib.decompressobj()
        compressed = data[cursor:-20]
        try:
            body = decoder.decompress(compressed, size + 1)
        except zlib.error:
            raise PackError() from None
        if not decoder.eof or decoder.unconsumed_tail or len(body) != size:
            raise PackError()
        cursor += len(compressed) - len(decoder.unused_data)
        total += len(body)
        if total > MAX_TOTAL_BYTES:
            raise PackError('git_pack_limit')
        records.append((offset, kind, base, body))
    if cursor != len(data) - 20:
        raise PackError()
    by_offset, by_oid = {}, {}
    remaining = list(records)
    expanded = 0
    # 有界轮次解析向前REF_DELTA；无进展即缺失或循环，不能返回部分对象。
    for _ in range(MAX_DELTA_DEPTH + 1):
        pending = []
        for offset, kind, base, body in remaining:
            depth = 0
            if kind in (6, 7):
                resolved = by_offset.get(base) if kind == 6 else by_oid.get(base)
                if resolved is None:
                    pending.append((offset, kind, base, body))
                    continue
                parent, depth = resolved
                depth += 1
                if depth > MAX_DELTA_DEPTH:
                    raise PackError('git_pack_limit')
                name, body = parent.kind, apply_delta(parent.body, body)
            else:
                name = {1: 'commit', 2: 'tree', 3: 'blob', 4: 'tag'}[kind]
            expanded += len(body)
            if expanded > MAX_TOTAL_BYTES:
                raise PackError('git_pack_limit')
            oid = sha1(f'{name} {len(body)}'.encode() + b'\0' + body).hexdigest()
            obj = PackedObject(name, oid, body)
            by_offset[offset] = (obj, depth)
            by_oid[oid] = (obj, depth)
        if not pending:
            return tuple(by_offset[offset][0] for offset, *_ in records)
        if len(pending) == len(remaining):
            raise PackError('git_pack_missing_base')
        remaining = pending
    raise PackError('git_pack_limit')
