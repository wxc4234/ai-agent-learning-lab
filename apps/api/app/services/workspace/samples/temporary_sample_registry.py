"""进程内自建样例登记；不接受路径注册，不替代用户或数据库资源授权。"""

import os
import secrets
import stat
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from app.services.workspace.samples.temporary_proposal_sample import (
    TemporaryProposalSample,
    temporary_proposal_sample,
)

MAX_REGISTERED_SAMPLES = 32


class SampleRegistryError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(repr=False)
class _Entry:
    manager: AbstractContextManager[TemporaryProposalSample]
    sample: TemporaryProposalSample
    root_identity: tuple[int, int]
    parent_identity: tuple[int, int]
    state: str = 'active'
    borrowed: bool = False


@dataclass(frozen=True)
class SampleHandle:
    # 内部随机句柄不输出到repr；不是可以传给模型或浏览器的授权令牌。
    token: str = field(repr=False)


class TemporarySampleRegistry:
    """单进程、有界、独占借用。close立即撤销新借用，最后使用者负责清理。

    进程重启/新实例不恢复登记，fork后的对象拒绝使用。调用方不得将借用
    路径交给超过借用作用域的后台任务；无法撤销已复制的Python路径对象。
    清理失败保留失效登记和现场，不自动重试；容量占用也保留。
    """

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._lock = RLock()
        self._entries: dict[str, _Entry] = {}

    def _require_process(self) -> None:
        # fork后不接触继承的锁，也不清理父进程仍在使用的目录。
        if os.getpid() != self._pid:
            raise SampleRegistryError('sample_registry_process_changed')

    def _entry(self, handle: SampleHandle) -> _Entry:
        if type(handle) is not SampleHandle or type(handle.token) is not str:
            raise SampleRegistryError('sample_registration_unavailable')
        entry = self._entries.get(handle.token)
        if entry is None:
            raise SampleRegistryError('sample_registration_unavailable')
        return entry

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise SampleRegistryError('sample_registration_unavailable')
        return info.st_dev, info.st_ino

    def create(self) -> SampleHandle:
        self._require_process()
        with self._lock:
            if len(self._entries) >= MAX_REGISTERED_SAMPLES:
                raise SampleRegistryError('sample_registry_capacity_exceeded')
            token = secrets.token_hex(32)
            if token in self._entries:
                raise SampleRegistryError('sample_registration_unavailable')

            # 只有这条内部创建路径能进入登记表，不提供 register(path/sample)。
            manager = temporary_proposal_sample()
            sample = manager.__enter__()
            try:
                # 路径仅用于复核；登记基准必须来自创建时持有的描述符。
                if (
                    self._identity(sample.root) != sample.root_identity
                    or self._identity(sample.root.parent) != sample.parent_identity
                ):
                    raise SampleRegistryError('sample_registration_unavailable')
                entry = _Entry(
                    manager=manager,
                    sample=sample,
                    root_identity=sample.root_identity,
                    parent_identity=sample.parent_identity,
                )
                self._entries[token] = entry
            except BaseException as error:
                manager.__exit__(type(error), error, error.__traceback__)
                raise
            return SampleHandle(token)

    def _dispose(self, token: str, entry: _Entry) -> None:
        # 在清理前失效；失败绝不能重新开放借用。调用时必须持锁且无借用。
        entry.state = 'failed'
        entry.manager.__exit__(None, None, None)
        del self._entries[token]

    @contextmanager
    def borrow(self, handle: SampleHandle) -> Generator[TemporaryProposalSample, None, None]:
        self._require_process()
        with self._lock:
            entry = self._entry(handle)
            if entry.state != 'active' or entry.borrowed:
                raise SampleRegistryError('sample_registration_unavailable')
            try:
                valid = (
                    self._identity(entry.sample.root) == entry.root_identity
                    and self._identity(entry.sample.root.parent) == entry.parent_identity
                    and entry.sample.root.stat(follow_symlinks=False).st_uid == os.geteuid()
                    and stat.S_IMODE(entry.sample.root.stat(follow_symlinks=False).st_mode) == 0o700
                )
            except (OSError, SampleRegistryError):
                valid = False
            if not valid:
                try:
                    self._dispose(handle.token, entry)
                except Exception:  # noqa: BLE001 -- 固定错误并明确清理未完成，不输出路径
                    error = SampleRegistryError('sample_registration_unavailable')
                    error.add_note('sample_cleanup_incomplete')
                    raise error from None
                raise SampleRegistryError('sample_registration_unavailable')
            entry.borrowed = True
        active_error = None
        try:
            yield entry.sample
        except BaseException as error:
            active_error = error
            raise
        finally:
            # 子进程不得归还/清理从父进程继承的借用。
            if os.getpid() == self._pid:
                with self._lock:
                    entry.borrowed = False
                    if active_error is not None:
                        entry.state = 'closing'
                    if entry.state == 'closing':
                        try:
                            self._dispose(handle.token, entry)
                        except BaseException:
                            if active_error is None:
                                raise
                            active_error.add_note('sample_cleanup_incomplete')

    def close(self, handle: SampleHandle) -> bool:
        """返回True表示清理完成；False表示已撤销并等待当前借用退出。"""
        self._require_process()
        with self._lock:
            entry = self._entry(handle)
            if entry.state == 'failed':
                raise SampleRegistryError('sample_registration_unavailable')
            entry.state = 'closing'
            if entry.borrowed:
                return False
            self._dispose(handle.token, entry)
            return True
