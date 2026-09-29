"""单个SHA-1 loose tree的纯解析；不读取引用对象、不访问文件系统。

封装：https://git-scm.com/book/en/v2/Git-Internals-Git-Objects
条目与排序：https://github.com/git/git/blob/master/tree-walk.c 和 tree.c
仅支持规范模式、严格UTF-8及保守单组件名称；不声称兼容所有Git树。
"""

import re
import zlib
from dataclasses import dataclass
from hashlib import sha1

MAX_COMPRESSED_BYTES = 256 * 1024
MAX_OBJECT_BYTES = 1024 * 1024
MAX_TREE_ENTRIES = 2000
MAX_NAME_BYTES = 4096
_MODES = {b'40000': 0o40000, b'100644': 0o100644, b'100755': 0o100755,
          b'120000': 0o120000, b'160000': 0o160000}


class LooseTreeError(ValueError):
    """固定错误分类，不回显名称、压缩内容或底层异常。"""

    def __init__(self, code: str = 'loose_tree_invalid') -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TreeEntry:
    name: str
    mode: int
    # 仅为引用标识；不证明对象存在、类型正确或允许访问对应路径。
    object_id: str


@dataclass(frozen=True, slots=True)
class LooseTree:
    object_id: str
    body_bytes: int
    entries: tuple[TreeEntry, ...]


def parse_loose_tree(compressed: bytes, *, expected_oid: str) -> LooseTree:
    """完整验证才返回不可变投影；所有预算均包含于本次调用，无数据库事务。"""
    if (type(compressed) is not bytes or type(expected_oid) is not str
            or re.fullmatch(r'[0-9a-f]{40}', expected_oid) is None or expected_oid == '0' * 40):
        raise LooseTreeError('loose_tree_input_invalid')
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise LooseTreeError('loose_tree_limit')
    decoder = zlib.decompressobj()
    try:
        # 上限包含对象头；额外一个哨兵字节检测压缩炸弹，不调用无界flush。
        raw = decoder.decompress(compressed, MAX_OBJECT_BYTES + 1)
    except zlib.error:
        raise LooseTreeError('loose_tree_compression_invalid') from None
    if len(raw) > MAX_OBJECT_BYTES or decoder.unconsumed_tail:
        raise LooseTreeError('loose_tree_limit')
    if not decoder.eof or decoder.unused_data:
        raise LooseTreeError('loose_tree_compression_invalid')
    separator = raw.find(b'\0', 0, 32)
    if separator < 0 or re.fullmatch(rb'tree (0|[1-9][0-9]{0,9})', raw[:separator]) is None:
        raise LooseTreeError()
    body = raw[separator + 1:]
    if int(raw[5:separator]) != len(body):
        raise LooseTreeError('loose_tree_size_mismatch')
    # Git命名校验不是签名验证；可信expected_oid仍需由调用方授权获得。
    if sha1(raw).hexdigest() != expected_oid:
        raise LooseTreeError('loose_tree_hash_mismatch')
    entries: list[TreeEntry] = []
    names: set[bytes] = set()
    previous = b''
    offset = 0
    while offset < len(body):
        if len(entries) >= MAX_TREE_ENTRIES:
            raise LooseTreeError('loose_tree_limit')
        space = body.find(b' ', offset, offset + 7)
        mode = _MODES.get(body[offset:space]) if space >= 0 else None
        if mode is None:
            raise LooseTreeError('loose_tree_mode_unsupported')
        start = space + 1
        end = body.find(b'\0', start, start + MAX_NAME_BYTES + 1)
        if end < 0 or end + 21 > len(body):
            raise LooseTreeError()
        name_bytes = body[start:end]
        try:
            name = name_bytes.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            raise LooseTreeError('loose_tree_encoding_unsupported') from None
        if (not name or name in ('.', '..') or name.lower() == '.git'
                or any(ch in '/\\:' or ord(ch) < 32 or ord(ch) == 127 for ch in name)):
            raise LooseTreeError('loose_tree_name_unsupported')
        # 重复名可能被foo.txt等条目隔开，不能只比较相邻名称。
        key = name_bytes + (b'/' if mode == 0o40000 else b'')
        if name_bytes in names or key <= previous:
            raise LooseTreeError('loose_tree_order_invalid')
        oid = body[end + 1:end + 21]
        if oid == b'\0' * 20:
            raise LooseTreeError('loose_tree_object_id_unsupported')
        entries.append(TreeEntry(name, mode, oid.hex()))
        names.add(name_bytes)
        previous = key
        offset = end + 21
    return LooseTree(expected_oid, len(body), tuple(entries))
