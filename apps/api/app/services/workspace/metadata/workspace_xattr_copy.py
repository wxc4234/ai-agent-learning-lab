"""可信临时目标的受限xattr复制；不创建文件、不执行replace或清理调用方资源。"""

import ctypes
import os
import stat
import sys
from dataclasses import dataclass
from functools import lru_cache

from app.services.workspace.metadata.workspace_file_xattrs import FileXattr, read_file_xattrs


class XattrCopyError(ValueError):
    """固定错误码；失败可能已经部分修改临时目标，调用方必须清理。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class XattrCopyResult:
    written_count: int
    verified_count: int


@lru_cache(maxsize=1)
def _native_library() -> ctypes.CDLL:
    if sys.platform != 'darwin':
        raise XattrCopyError('xattr_copy_unsupported')
    try:
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        library.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        library.fsetxattr.restype = ctypes.c_int
    except (OSError, AttributeError) as error:
        raise XattrCopyError('xattr_copy_unsupported') from error
    return library


def _target_version(metadata: os.stat_result) -> tuple[int, ...]:
    # 设置属性允许ctime变化；其余对象身份、内容信息与权限不得改变。
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_uid, metadata.st_gid, metadata.st_nlink,
            metadata.st_size, metadata.st_mtime_ns, metadata.st_flags)


def copy_file_xattrs(
    *, source_fd: int, target_fd: int, expected: tuple[FileXattr, ...],
) -> XattrCopyResult:
    """调用方保证目标是独占临时文件，期间不关闭/复用描述符且无其他写者。

    描述符本身不能证明临时性或路径授权；此函数不能作为公开接口。
    成功仅表示本次可见快照核对通过，不代表原子快照或持久化提交。
    """

    if (
        type(source_fd) is not int or source_fd < 0
        or type(target_fd) is not int or target_fd < 0
        or type(expected) is not tuple
        or any(type(item) is not FileXattr for item in expected)
    ):
        raise XattrCopyError('xattr_copy_input_invalid')
    library = _native_library()
    try:
        source_before = os.fstat(source_fd)
        target_before = os.fstat(target_fd)
        if (
            not stat.S_ISREG(source_before.st_mode)
            or not stat.S_ISREG(target_before.st_mode)
            or (source_before.st_dev, source_before.st_ino)
            == (target_before.st_dev, target_before.st_ino)
            or target_before.st_nlink not in (0, 1)
            or target_before.st_uid != os.geteuid()
            or target_before.st_flags != 0
            or target_before.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
        ):
            raise XattrCopyError('xattr_copy_target_unsupported')

        # 只把本次受预算约束的真实读取值交给原生接口，expected仅作基线比较。
        source = read_file_xattrs(source_fd)
        if source != expected:
            raise XattrCopyError('xattr_copy_source_changed')
        target = read_file_xattrs(target_fd)
        source_values = {item.name: item.value for item in source}
        target_values = {item.name: item.value for item in target}
        if target_values.keys() - source_values.keys():
            raise XattrCopyError('xattr_copy_extra_target_attributes')

        # 在首次写入前再核对两个对象；无数据库事务，不承诺文件系统CAS。
        if read_file_xattrs(source_fd) != source or read_file_xattrs(target_fd) != target:
            raise XattrCopyError('xattr_copy_snapshot_changed')
        if (
            _target_version(os.fstat(source_fd)) != _target_version(source_before)
            or os.fstat(source_fd).st_ctime_ns != source_before.st_ctime_ns
            or _target_version(os.fstat(target_fd)) != _target_version(target_before)
        ):
            raise XattrCopyError('xattr_copy_snapshot_changed')

        written = 0
        for item in source:
            # 已相同的值只验证、不重设；这不是对系统属性的忽略或删除。
            if item.name in target_values and target_values[item.name] == item.value:
                continue
            buffer = ctypes.create_string_buffer(item.value, max(len(item.value), 1))
            if library.fsetxattr(target_fd, item.name, buffer, len(item.value), 0, 0) != 0:
                raise XattrCopyError('xattr_copy_set_failed')
            written += 1

        if read_file_xattrs(target_fd) != source:
            raise XattrCopyError('xattr_copy_readback_mismatch')
        if read_file_xattrs(source_fd) != source:
            raise XattrCopyError('xattr_copy_source_changed')
        source_after = os.fstat(source_fd)
        if (
            _target_version(source_after) != _target_version(source_before)
            or source_after.st_ctime_ns != source_before.st_ctime_ns
            or _target_version(os.fstat(target_fd)) != _target_version(target_before)
        ):
            raise XattrCopyError('xattr_copy_snapshot_changed')
        return XattrCopyResult(written_count=written, verified_count=len(source))
    except OSError as error:
        raise XattrCopyError('xattr_copy_unavailable') from error
