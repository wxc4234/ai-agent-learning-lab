"""macOS扩展属性的有界只读快照；不授权路径、不复制或删除属性。"""

import ctypes
import errno
import os
import stat
import sys
from dataclasses import dataclass, field
from functools import lru_cache


# 使用描述符接口，并请求显示压缩相关属性。
XATTR_SHOWCOMPRESSION = 0x0020

MAX_XATTR_COUNT = 128
MAX_XATTR_NAME_BYTES = 127
MAX_XATTR_NAMES_BYTES = 16 * 1024
MAX_XATTR_VALUE_BYTES = 64 * 1024
MAX_XATTR_TOTAL_BYTES = 256 * 1024


class FileXattrError(ValueError):
    """只提供固定错误码，不暴露属性名称、属性值或底层异常。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FileXattr:
    # 名称使用原始UTF-8字节，避免解码再编码改变系统调用参数。
    name: bytes = field(repr=False)

    # 属性值是任意二进制数据，不能假设为文本，也不进入repr。
    value: bytes = field(repr=False)


@lru_cache(maxsize=1)
def _native_library() -> ctypes.CDLL:
    """独立声明本模块需要的ABI，不改变已有替换模块的能力要求。"""

    if sys.platform != "darwin":
        raise FileXattrError("file_xattr_unsupported")

    try:
        library = ctypes.CDLL(
            "/usr/lib/libSystem.B.dylib",
            use_errno=True,
        )

        library.flistxattr.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        library.flistxattr.restype = ctypes.c_ssize_t

        library.fgetxattr.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        library.fgetxattr.restype = ctypes.c_ssize_t
    except (OSError, AttributeError) as error:
        raise FileXattrError("file_xattr_unsupported") from error

    return library


def _checked_result(result: int) -> int:
    """失败不能当作空属性；变化与不可读取分别使用固定分类。"""

    if result >= 0:
        return result

    error_number = ctypes.get_errno()

    # 名称消失或缓冲区不足通常意味着探测与读取之间发生变化。
    if error_number in (
        errno.ERANGE,
        getattr(errno, "ENOATTR", 93),
    ):
        raise FileXattrError("file_xattr_changed")

    if error_number == errno.ENOTSUP:
        raise FileXattrError("file_xattr_unsupported")

    raise FileXattrError("file_xattr_unavailable")


def _file_version(descriptor: int) -> tuple[int, ...]:
    """检查已打开的普通文件，不重新解析路径，也不取得描述符所有权。"""

    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise FileXattrError("file_xattr_unsupported")

    # 不比较atime；读取可能影响访问时间。
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_flags,
    )


def _read_names(
    library: ctypes.CDLL,
    descriptor: int,
) -> tuple[bytes, ...]:
    size = _checked_result(
        library.flistxattr(
            descriptor,
            None,
            0,
            XATTR_SHOWCOMPRESSION,
        )
    )
    if size > MAX_XATTR_NAMES_BYTES:
        raise FileXattrError("file_xattr_limit_exceeded")

    # 即使探测为空，也实际读取一次。
    # 使用非零缓冲区，避免把第二次调用再次变成长度探测。
    capacity = max(size, 1)
    buffer = ctypes.create_string_buffer(capacity)
    actual = _checked_result(
        library.flistxattr(
            descriptor,
            buffer,
            capacity,
            XATTR_SHOWCOMPRESSION,
        )
    )
    if actual != size:
        raise FileXattrError("file_xattr_changed")

    if actual == 0:
        return ()

    raw = buffer.raw[:actual]
    if not raw.endswith(b"\x00"):
        raise FileXattrError("file_xattr_invalid")

    names = raw[:-1].split(b"\x00")
    if len(names) > MAX_XATTR_COUNT:
        raise FileXattrError("file_xattr_limit_exceeded")

    if len(set(names)) != len(names):
        raise FileXattrError("file_xattr_invalid")

    for name in names:
        if not name or len(name) > MAX_XATTR_NAME_BYTES:
            raise FileXattrError("file_xattr_invalid")
        try:
            name.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise FileXattrError("file_xattr_invalid") from error

    # 系统不保证名称顺序；排序后再比较，避免把顺序变化误判为内容变化。
    return tuple(sorted(names))


def _read_value(
    library: ctypes.CDLL,
    descriptor: int,
    name: bytes,
    remaining: int,
) -> bytes:
    size = _checked_result(
        library.fgetxattr(
            descriptor,
            name,
            None,
            0,
            0,
            XATTR_SHOWCOMPRESSION,
        )
    )

    # 在分配和读取之前检查单值及剩余总预算。
    if size > MAX_XATTR_VALUE_BYTES or size > remaining:
        raise FileXattrError("file_xattr_limit_exceeded")

    capacity = max(size, 1)
    buffer = ctypes.create_string_buffer(capacity)
    actual = _checked_result(
        library.fgetxattr(
            descriptor,
            name,
            buffer,
            capacity,
            0,
            XATTR_SHOWCOMPRESSION,
        )
    )
    if actual != size:
        raise FileXattrError("file_xattr_changed")

    # 再查长度，避免把读取期间增长后的部分内容当成完整属性。
    latest_size = _checked_result(
        library.fgetxattr(
            descriptor,
            name,
            None,
            0,
            0,
            XATTR_SHOWCOMPRESSION,
        )
    )
    if latest_size != size:
        raise FileXattrError("file_xattr_changed")

    return buffer.raw[:actual]


def _read_snapshot(
    library: ctypes.CDLL,
    descriptor: int,
) -> tuple[FileXattr, ...]:
    names = _read_names(library, descriptor)

    # 名称的终止符也计入逻辑数据预算。
    used = sum(len(name) + 1 for name in names)
    if used > MAX_XATTR_TOTAL_BYTES:
        raise FileXattrError("file_xattr_limit_exceeded")

    attributes = []
    for name in names:
        value = _read_value(
            library,
            descriptor,
            name,
            MAX_XATTR_TOTAL_BYTES - used,
        )
        used += len(value)
        attributes.append(FileXattr(name=name, value=value))

    # 读取值期间可能新增或删除属性，结束时再次核对名称集合。
    if _read_names(library, descriptor) != names:
        raise FileXattrError("file_xattr_changed")

    return tuple(attributes)


def read_file_xattrs(descriptor: int) -> tuple[FileXattr, ...]:
    """读取调用方已安全打开的描述符；不关闭它，也不修改文件。"""

    if type(descriptor) is not int or descriptor < 0:
        raise FileXattrError("file_xattr_descriptor_invalid")

    library = _native_library()

    try:
        before = _file_version(descriptor)
        first = _read_snapshot(library, descriptor)
        middle = _file_version(descriptor)

        if middle != before:
            raise FileXattrError("file_xattr_changed")

        # 固定第二轮用于发现同长度属性值变化，不循环重试。
        second = _read_snapshot(library, descriptor)
        after = _file_version(descriptor)

        if after != before or second != first:
            raise FileXattrError("file_xattr_changed")

        return first
    except OSError as error:
        # fstat等Python系统调用异常也不能泄露底层信息。
        raise FileXattrError("file_xattr_unavailable") from error
