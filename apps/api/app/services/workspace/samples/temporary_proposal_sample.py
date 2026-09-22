"""服务端自建临时样例生命周期；不是资源登记或文件写入授权。"""

import os
import stat
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

SAMPLE_FILENAME = 'example.txt'
SAMPLE_CONTENT = b'old\n'


class TemporarySampleError(ValueError):
    """只公开固定错误码；清理失败时保留现场，不递归删除。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TemporaryProposalSample:
    # 路径仅供可信内部调用使用，不进入repr或公开响应。
    root: Path = field(repr=False)
    root_identity: tuple[int, int] = field(repr=False)
    relative_path: str = SAMPLE_FILENAME


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _check_root(parent_fd: int, name: str, root_fd: int, identity: tuple[int, int]) -> None:
    linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    opened = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(linked.st_mode)
        or _identity(linked) != identity
        or _identity(opened) != identity
        or opened.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o700
    ):
        raise TemporarySampleError('sample_identity_changed')


def _cleanup(parent_fd: int, name: str, root_fd: int, identity: tuple[int, int]) -> None:
    _check_root(parent_fd, name, root_fd, identity)
    entries = os.listdir(root_fd)
    if set(entries) - {SAMPLE_FILENAME}:
        raise TemporarySampleError('sample_cleanup_incomplete')
    if SAMPLE_FILENAME in entries:
        info = os.stat(SAMPLE_FILENAME, dir_fd=root_fd, follow_symlinks=False)
        # 允许可信执行器原子替换样例文件；不跟随链接或递归处理子目录。
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise TemporarySampleError('sample_cleanup_incomplete')
        os.unlink(SAMPLE_FILENAME, dir_fd=root_fd)
    _check_root(parent_fd, name, root_fd, identity)
    os.rmdir(name, dir_fd=parent_fd)


@contextmanager
def temporary_proposal_sample() -> Generator[TemporaryProposalSample, None, None]:
    """无路径参数，只在服务端临时目录创建固定样例。

    可信调用方须在作用域内结束全部使用，再允许退出清理；不支持后台任务
    跨作用域持有路径。目录须无外部写者，身份检查不是文件系统CAS。
    返回对象可伪造、路径可过期，不能作为未来HTTP门禁的授权凭证。
    """

    if sys.platform != 'darwin':
        raise TemporarySampleError('sample_platform_unsupported')
    # 系统临时目录由服务端环境决定，不接受浏览器传入的目录。
    name = 'agent-proposal-' + uuid4().hex
    try:
        parent = Path(tempfile.gettempdir()).resolve(strict=True)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise TemporarySampleError('sample_creation_failed') from None
    root_fd = None
    owned = False
    identity = None
    active_error = None
    yielded = False
    try:
        # mkdir是独占创建；名称碰撞时不重试、不清理已有目录。
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        owned = True
        identity = _identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
        root_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        if _identity(os.fstat(root_fd)) != identity:
            raise TemporarySampleError('sample_identity_changed')
        os.fchmod(root_fd, 0o700)
        _check_root(parent_fd, name, root_fd, identity)
        descriptor = os.open(SAMPLE_FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=root_fd)
        try:
            os.fchmod(descriptor, 0o600)
            remaining = memoryview(SAMPLE_CONTENT)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise TemporarySampleError('sample_creation_failed')
                remaining = remaining[written:]
        finally:
            os.close(descriptor)
        _check_root(parent_fd, name, root_fd, identity)
        # 路径发布前核对可见父目录仍对应持有的描述符。
        if _identity(os.stat(parent, follow_symlinks=False)) != _identity(os.fstat(parent_fd)):
            raise TemporarySampleError('sample_identity_changed')
        yielded = True
        yield TemporaryProposalSample(root=parent / name, root_identity=identity)
    except OSError as error:
        if yielded:
            active_error = error
            raise
        active_error = TemporarySampleError('sample_creation_failed')
        raise active_error from None
    except BaseException as error:
        # 中断也必须释放资源；清理错误不能抹去最初的执行异常。
        active_error = error
        raise
    finally:
        try:
            if owned:
                if root_fd is not None and identity is not None:
                    _cleanup(parent_fd, name, root_fd, identity)
                elif identity is not None:
                    # 打开目录失败时只尝试移除同一个空目录，绝不递归删除。
                    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if _identity(current) != identity or not stat.S_ISDIR(current.st_mode):
                        raise TemporarySampleError('sample_identity_changed')
                    os.rmdir(name, dir_fd=parent_fd)
                else:
                    raise TemporarySampleError('sample_cleanup_incomplete')
        except (OSError, TemporarySampleError):
            if active_error is not None:
                active_error.add_note('sample_cleanup_incomplete')
            else:
                raise TemporarySampleError('sample_cleanup_incomplete') from None
        finally:
            try:
                if root_fd is not None:
                    os.close(root_fd)
            finally:
                os.close(parent_fd)
