"""macOS 临时样例的文件元数据保护，不负责路径授权或文件替换。"""

import ctypes
import errno
import os
import stat
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

from app.services.workspace.metadata.workspace_file_xattrs import (
    FileXattr,
    FileXattrError,
    read_file_xattrs,
)
from app.services.workspace.metadata.workspace_xattr_copy import (
    XattrCopyError,
    copy_file_xattrs,
)


# 来自 macOS SDK 的 sys/acl.h；xattr ABI 由专用模块管理。
ACL_TYPE_EXTENDED = 0x00000100

# 对系统返回的数据设置上限，不按未经检查的长度分配内存。
MAX_ACL_BYTES = 64 * 1024


class FileMetadataError(ValueError):
    """只传递固定错误码，不暴露宿主路径或原生错误信息。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FileMetadata:
    mode: int
    uid: int
    gid: int

    # 保存系统导出的 ACL 表示；不保留原生指针，也不打印属性正文。
    acl: bytes = field(repr=False)

    # 保存有界读取的完整可见属性，包含系统属性，不做名称过滤。
    xattrs: tuple[FileXattr, ...] = field(repr=False)


@lru_cache(maxsize=1)
def _native_library() -> ctypes.CDLL:
    """仅支持已明确实现的 macOS 接口，不猜测其他平台的 ABI。"""

    if sys.platform != "darwin":
        raise FileMetadataError("file_replace_metadata_unsupported")

    try:
        library = ctypes.CDLL(
            "/usr/lib/libSystem.B.dylib",
            use_errno=True,
        )

        library.acl_get_fd_np.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
        ]
        library.acl_get_fd_np.restype = ctypes.c_void_p

        library.acl_init.argtypes = [ctypes.c_int]
        library.acl_init.restype = ctypes.c_void_p

        library.acl_free.argtypes = [ctypes.c_void_p]
        library.acl_free.restype = ctypes.c_int

        library.acl_size.argtypes = [ctypes.c_void_p]
        library.acl_size.restype = ctypes.c_ssize_t

        library.acl_copy_ext.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ssize_t,
        ]
        library.acl_copy_ext.restype = ctypes.c_ssize_t

        library.acl_set_fd_np.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        library.acl_set_fd_np.restype = ctypes.c_int

    except (OSError, AttributeError) as error:
        raise FileMetadataError(
            "file_replace_metadata_unsupported",
        ) from error

    return library


@contextmanager
def _opened_acl(
    library: ctypes.CDLL,
    descriptor: int,
) -> Generator[int, None, None]:
    """原生 ACL 对象只在当前作用域存活，任何退出路径都释放。"""

    ctypes.set_errno(0)
    pointer = library.acl_get_fd_np(
        descriptor,
        ACL_TYPE_EXTENDED,
    )

    if not pointer:
        # macOS 用 ENOENT 表示没有 ACL 属性。
        # 在本策略中，将其规范化为没有条目、没有标志的空 ACL。
        if ctypes.get_errno() != errno.ENOENT:
            raise FileMetadataError("file_replace_metadata_unavailable")

        pointer = library.acl_init(0)
        if not pointer:
            raise FileMetadataError("file_replace_metadata_unavailable")

    try:
        yield pointer
    finally:
        if library.acl_free(pointer) != 0:
            raise FileMetadataError("file_replace_metadata_unavailable")


def _acl_bytes(library: ctypes.CDLL, pointer: int) -> bytes:
    """通过系统导出 ACL，避免解析文本或反序列化外部输入。"""

    size = library.acl_size(pointer)
    if not 0 < size <= MAX_ACL_BYTES:
        raise FileMetadataError("file_replace_metadata_unsupported")

    buffer = ctypes.create_string_buffer(size)
    written = library.acl_copy_ext(buffer, pointer, size)
    if written != size:
        raise FileMetadataError("file_replace_metadata_unavailable")

    return buffer.raw


def _require_supported_file(descriptor: int) -> os.stat_result:
    """限制为当前用户拥有、单硬链接、无特殊标志的普通文件。"""

    if type(descriptor) is not int or descriptor < 0:
        raise FileMetadataError("file_replace_metadata_unsupported")

    current = os.fstat(descriptor)
    flags = getattr(current, "st_flags", None)

    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or current.st_uid != os.geteuid()
        or flags is None
        or flags != 0
        or current.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    ):
        raise FileMetadataError("file_replace_metadata_unsupported")

    return current


def _file_version(current: os.stat_result) -> tuple:
    """读取或复制期间，源对象的身份、内容信息和权限必须保持稳定。"""

    return (
        current.st_dev,
        current.st_ino,
        current.st_mode,
        current.st_uid,
        current.st_gid,
        current.st_nlink,
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
        current.st_flags,
    )


def _target_content_version(current: os.stat_result) -> tuple:
    """目标允许权限、属组和 ctime 改变，但不能换对象或改正文。"""

    return (
        current.st_dev,
        current.st_ino,
        stat.S_IFMT(current.st_mode),
        current.st_uid,
        current.st_nlink,
        current.st_size,
        current.st_mtime_ns,
        current.st_flags,
    )


def read_file_metadata(descriptor: int) -> FileMetadata:
    """读取完整可见元数据；固定两轮核对，不重试或降级为空属性。"""

    library = _native_library()

    try:
        before = _require_supported_file(descriptor)

        with _opened_acl(library, descriptor) as pointer:
            first_acl = _acl_bytes(library, pointer)
        first_xattrs = read_file_xattrs(descriptor)

        middle = _require_supported_file(descriptor)

        with _opened_acl(library, descriptor) as pointer:
            second_acl = _acl_bytes(library, pointer)
        second_xattrs = read_file_xattrs(descriptor)

        after = _require_supported_file(descriptor)

        if (
            _file_version(before) != _file_version(middle)
            or _file_version(middle) != _file_version(after)
            or first_acl != second_acl
            or first_xattrs != second_xattrs
        ):
            raise FileMetadataError("file_replace_metadata_changed")

        return FileMetadata(
            mode=stat.S_IMODE(after.st_mode),
            uid=after.st_uid,
            gid=after.st_gid,
            acl=second_acl,
            xattrs=second_xattrs,
        )
    except FileXattrError as error:
        # 无法取得完整快照就拒绝，不把读取失败解释成属性不存在。
        raise FileMetadataError(
            "file_replace_metadata_unavailable",
        ) from error
    except OSError as error:
        raise FileMetadataError(
            "file_replace_metadata_unavailable",
        ) from error


def verify_file_metadata(
    descriptor: int,
    expected: FileMetadata,
) -> None:
    if read_file_metadata(descriptor) != expected:
        raise FileMetadataError("file_replace_metadata_changed")


def copy_file_metadata(
    source_fd: int,
    target_fd: int,
    expected: FileMetadata,
) -> None:
    """只修改调用方独占的临时目标，不负责授权、提交或清理。

    调用方保证描述符生命周期稳定，目标没有其他写者。
    失败可能留下部分元数据修改，不回滚、不关闭调用方描述符。
    """

    if type(expected) is not FileMetadata:
        raise FileMetadataError("file_replace_metadata_unsupported")

    library = _native_library()

    try:
        source_before = _require_supported_file(source_fd)
        target_before = _require_supported_file(target_fd)

        if (
            source_before.st_dev,
            source_before.st_ino,
        ) == (
            target_before.st_dev,
            target_before.st_ino,
        ):
            raise FileMetadataError("file_replace_metadata_unsupported")

        # 写入前重新读取源；expected 只是比较基线，不是写入授权。
        verify_file_metadata(source_fd, expected)
        target_metadata = read_file_metadata(target_fd)

        if target_metadata.uid != expected.uid:
            raise FileMetadataError("file_replace_metadata_unsupported")

        # 目标多出的属性不能偷偷删除；在首次修改目标前就拒绝。
        source_names = {item.name for item in expected.xattrs}
        target_names = {item.name for item in target_metadata.xattrs}
        if target_names - source_names:
            raise FileMetadataError("file_replace_metadata_unsupported")

        if (
            _file_version(_require_supported_file(source_fd))
            != _file_version(source_before)
            or _file_version(_require_supported_file(target_fd))
            != _file_version(target_before)
        ):
            raise FileMetadataError("file_replace_metadata_changed")

        # 此处没有数据库事务，也没有文件提交。
        # chown 可能影响权限位，因此先处理属组，再设置权限。
        if target_metadata.gid != expected.gid:
            os.fchown(target_fd, -1, expected.gid)

        os.fchmod(target_fd, expected.mode)

        # 在安装最终 ACL 之前复制属性，避免 ACL 提前限制属性写入。
        # 原语自行重新读取源、拒绝目标额外属性，并验证完整回读。
        copy_file_xattrs(
            source_fd=source_fd,
            target_fd=target_fd,
            expected=expected.xattrs,
        )

        # 只使用重新取得的原生 ACL 对象。
        # 不把保存的 bytes 交给没有长度参数的反序列化接口。
        with _opened_acl(library, source_fd) as pointer:
            if _acl_bytes(library, pointer) != expected.acl:
                raise FileMetadataError("file_replace_metadata_changed")

            # 覆盖临时目标可能从父目录继承的 ACL。
            if library.acl_set_fd_np(
                target_fd,
                pointer,
                ACL_TYPE_EXTENDED,
            ) != 0:
                raise FileMetadataError(
                    "file_replace_metadata_unavailable",
                )

        # ACL、权限和属性可能互相影响，必须在全部设置结束后一起核对。
        verify_file_metadata(target_fd, expected)
        verify_file_metadata(source_fd, expected)

        source_after = _require_supported_file(source_fd)
        target_after = _require_supported_file(target_fd)

        if (
            _file_version(source_after) != _file_version(source_before)
            or _target_content_version(target_after)
            != _target_content_version(target_before)
        ):
            raise FileMetadataError("file_replace_metadata_changed")
    except (FileXattrError, XattrCopyError) as error:
        # 对上层保持统一的元数据错误边界，不泄露属性名称或值。
        raise FileMetadataError(
            "file_replace_metadata_unavailable",
        ) from error
    except OSError as error:
        raise FileMetadataError(
            "file_replace_metadata_unavailable",
        ) from error
