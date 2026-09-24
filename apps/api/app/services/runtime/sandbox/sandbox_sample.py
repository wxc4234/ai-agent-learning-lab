"""服务端自建的 Sandbox 挂载样例；不接受外部目录。"""

import errno
import os
import stat
import sys
import tempfile
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator
from typing import Literal
from uuid import uuid4


SAMPLE_DESTINATION = "/workspace"
SAMPLE_FILENAME = "example.txt"
SAMPLE_CONTENT = b"sandbox sample\n"
# 目标工厂独立校验输入预算，不能只依赖调用方已经限制来源大小。
MAX_SANDBOX_SNAPSHOT_BYTES = 256 * 1024


class SandboxSampleError(ValueError):
    """使用固定错误信息，不公开宿主路径或底层异常。"""

    def __init__(self) -> None:
        super().__init__("无法确认 Sandbox 样例来源或清理结果")


class SandboxSampleCreationUnconfirmed(SandboxSampleError):
    """仅携带本次独占创建后的内部定位信息，不提供清理或重试授权。"""

    def __init__(self, *, token: str, root: Path | None) -> None:
        # root=None 表示本次没有确认创建私有父目录，不能认领同名旧目录。
        self.token = token
        self.root = root
        super().__init__()


class _SamplePathMissing(SandboxSampleError):
    """打开原来源路径时明确不存在；不表示目录没有被移到别处。"""


class _SampleIdentityChanged(SandboxSampleError):
    """已观察到目录身份、链接、权限或固定目录结构不再匹配。"""


SandboxSampleStatus = Literal[
    "identity_matches_record", "missing", "identity_changed", "unregistered", "unconfirmed",
]


@dataclass(frozen=True, slots=True)
class SandboxSample:
    # 路径和身份只用于本进程内部核对，不进入公开响应。
    token: str
    root: Path = field(repr=False)
    parent_identity: tuple[int, int] = field(repr=False)
    root_identity: tuple[int, int] = field(repr=False)
    file_identity: tuple[int, int] = field(repr=False)

    # 原探针使用 0666 验证挂载层 EROFS；内容快照使用 0444。
    # 此字段不是权限凭据，仍要求工厂登记的同一个对象。
    file_mode: int = field(default=0o666, repr=False)


# 对象字段本身不是授权凭据。
# 必须是本进程工厂登记的同一个对象，且尚未完成清理。
_ACTIVE_SAMPLES: dict[str, SandboxSample] = {}


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _open_directory_without_links(path: Path) -> int:
    """逐级打开绝对目录，拒绝任意路径段中的符号链接。"""

    if not path.is_absolute() or ".." in path.parts:
        raise SandboxSampleError()

    descriptor = os.open(
        "/",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _checked_sample_directories(
    sample: SandboxSample,
) -> Iterator[tuple[int, int]]:
    """核对登记、路径和持有的描述符，再交给内部操作使用。"""

    if (
        not isinstance(sample, SandboxSample)
        or _ACTIVE_SAMPLES.get(sample.token) is not sample
    ):
        raise SandboxSampleError()

    parent_fd = None
    root_fd = None
    try:
        parent_fd = _open_directory_without_links(sample.root.parent)
        parent_info = os.fstat(parent_fd)
        # 先确认父目录，再打开子目录；父目录已被替换不能误报为来源缺失。
        if (
            _identity(parent_info) != sample.parent_identity
            or parent_info.st_uid != os.geteuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise _SampleIdentityChanged()
        root_fd = os.open(
            sample.root.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )

        root_info = os.fstat(root_fd)
        visible_parent = os.stat(sample.root.parent, follow_symlinks=False)
        linked_info = os.stat(
            sample.root.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        file_info = os.stat(
            SAMPLE_FILENAME,
            dir_fd=root_fd,
            follow_symlinks=False,
        )

        if (
            _identity(visible_parent) != sample.parent_identity
            or not stat.S_ISDIR(visible_parent.st_mode)
            or _identity(root_info) != sample.root_identity
            or _identity(linked_info) != sample.root_identity
            or not stat.S_ISDIR(linked_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or stat.S_IMODE(root_info.st_mode) != 0o755
            or not stat.S_ISREG(file_info.st_mode)
            or _identity(file_info) != sample.file_identity
            or file_info.st_uid != os.geteuid()
            or file_info.st_nlink != 1
            or sample.file_mode not in (0o444, 0o666)
            or stat.S_IMODE(file_info.st_mode) != sample.file_mode
            or set(os.listdir(root_fd)) != {SAMPLE_FILENAME}
            or set(os.listdir(parent_fd)) != {sample.root.name}
        ):
            raise _SampleIdentityChanged()

        yield parent_fd, root_fd
    except OSError as error:
        if error.errno == errno.ENOENT:
            # 目录尚未打开时是路径缺失；已打开后缺少声明条目/链接则为结构变化。
            error_type = _SamplePathMissing if root_fd is None else _SampleIdentityChanged
            raise error_type() from None
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _SampleIdentityChanged() from None
        raise SandboxSampleError() from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _create_sandbox_sample(
    *,
    content: bytes,
    file_mode: int,
) -> SandboxSample:
    """统一创建原探针与内容快照，不接受外部目录或文件名。"""

    # 所有纯输入检查都在文件系统副作用之前完成。
    if type(content) is not bytes:
        raise TypeError("快照内容必须是 bytes")
    if len(content) > MAX_SANDBOX_SNAPSHOT_BYTES:
        raise SandboxSampleError()
    if file_mode not in (0o444, 0o666):
        raise SandboxSampleError()
    if sys.platform != "darwin":
        raise SandboxSampleError()

    token = uuid4().hex
    parent_name = f"agent-sandbox-sample-{token}"
    root = None
    parent_created = False

    try:
        base = Path(tempfile.gettempdir()).resolve(strict=True)
        root = base / parent_name / "repository"

        # --mount 使用分隔符语法，来源路径不能改变其参数结构。
        if any(character in str(root) for character in ',\"\r\n\x00'):
            raise SandboxSampleError()

        # 描述符关闭也属于创建过程；全部正常关闭后才发布登记。
        with ExitStack() as stack:
            base_fd = _open_directory_without_links(base)
            stack.callback(os.close, base_fd)

            os.mkdir(parent_name, mode=0o700, dir_fd=base_fd)
            parent_created = True

            parent_fd = os.open(
                parent_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=base_fd,
            )
            stack.callback(os.close, parent_fd)
            os.fchmod(parent_fd, 0o700)

            os.mkdir("repository", mode=0o755, dir_fd=parent_fd)
            root_fd = os.open(
                "repository",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            stack.callback(os.close, root_fd)
            os.fchmod(root_fd, 0o755)

            file_fd = os.open(
                SAMPLE_FILENAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_fd,
            )
            stack.callback(os.close, file_fd)

            # 短写入必须继续；返回 0 不能视为复制完成。
            remaining = memoryview(content)
            while remaining:
                written = os.write(file_fd, remaining)
                if written <= 0:
                    raise SandboxSampleError()
                remaining = remaining[written:]

            os.fchmod(file_fd, file_mode)

            sample = SandboxSample(
                token=token,
                root=root,
                parent_identity=_identity(os.fstat(parent_fd)),
                root_identity=_identity(os.fstat(root_fd)),
                file_identity=_identity(os.fstat(file_fd)),
                file_mode=file_mode,
            )

        _ACTIVE_SAMPLES[token] = sample
        return sample

    except (OSError, SandboxSampleError):
        # 创建不是原子事务。失败保留本次独占创建的定位信息；
        # 未完整登记的现场不能交给常规清理函数，更不能递归盲删。
        raise SandboxSampleCreationUnconfirmed(
            token=token,
            root=root if parent_created else None,
        ) from None


def create_sandbox_sample() -> SandboxSample:
    """保留原无参数探针入口及其 EROFS 验证用途。"""

    return _create_sandbox_sample(
        content=SAMPLE_CONTENT,
        file_mode=0o666,
    )


def create_sandbox_snapshot(*, content: bytes) -> SandboxSample:
    """用内部调用者提供的有界字节创建独立只读内容快照。"""

    return _create_sandbox_sample(
        content=content,
        file_mode=0o444,
    )

def confirm_sandbox_sample_source(sample: SandboxSample) -> str:
    """返回本次核对过的源路径；结果不是长期有效的操作授权。"""

    with _checked_sample_directories(sample):
        return str(sample.root)


def observe_sandbox_sample(sample: SandboxSample | None) -> SandboxSampleStatus:
    """只读观察已登记样例；不重新登记、不清理，也不读取文件正文。

    未登记对象和仅有路径的部分创建现场均不能用来探测任意宿主目录。
    匹配只表示本次元数据检查通过，不等于内容快照或执行/清理授权。
    """

    if (
        not isinstance(sample, SandboxSample)
        or not isinstance(sample.token, str)
        or _ACTIVE_SAMPLES.get(sample.token) is not sample
    ):
        return "unregistered"
    try:
        with _checked_sample_directories(sample):
            return "identity_matches_record"
    except _SamplePathMissing:
        return "missing"
    except _SampleIdentityChanged:
        return "identity_changed"
    except Exception:  # noqa: BLE001 -- 诊断错误保持未知，绝不降级为缺失或匹配。
        return "unconfirmed"


def cleanup_sandbox_sample(sample: SandboxSample) -> None:
    """仅在调用方已确认所有使用者结束后，显式清理自有样例。"""

    # 文件系统与 Docker 不存在共同事务。
    # 调用方必须先确认自有容器缺失，再调用本函数。
    # 不能用 finally 无条件删除仍可能被容器引用的来源。
    try:
        with _checked_sample_directories(sample) as (parent_fd, root_fd):
            base_fd = _open_directory_without_links(sample.root.parent.parent)
            try:
                visible_parent = os.stat(
                    sample.root.parent.name,
                    dir_fd=base_fd,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(visible_parent.st_mode)
                    or _identity(visible_parent) != sample.parent_identity
                ):
                    raise SandboxSampleError()

                # 只删除已核对的一个普通文件和两个空目录。
                # 遇到替换、额外文件或未知状态时拒绝递归清理。
                os.unlink(SAMPLE_FILENAME, dir_fd=root_fd)
                os.rmdir(sample.root.name, dir_fd=parent_fd)
                os.rmdir(sample.root.parent.name, dir_fd=base_fd)
            finally:
                os.close(base_fd)
    except OSError:
        raise SandboxSampleError() from None

    # 只有清理完整成功，才撤销本进程登记。
    del _ACTIVE_SAMPLES[sample.token]
