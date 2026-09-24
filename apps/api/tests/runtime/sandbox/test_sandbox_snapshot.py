"""独立快照工厂：字节、权限、登记时序与失败现场。"""

import os
import stat

import pytest

from app.services.runtime.sandbox import sandbox_sample as samples
from tests.runtime.sandbox.test_sandbox_sample import (
    confirm,
    mounted_payload,
    sample_base as sample_base,  # noqa: PLC0414
)


@pytest.mark.parametrize('content', [b'', b'old\n', '中文\r\n'.encode(), b'\xef\xbb\xbf\x00\xff', b'x' * samples.MAX_SANDBOX_SNAPSHOT_BYTES])
def test_snapshot_bytes_permissions_policy_and_cleanup(sample_base, content):
    sample = samples.create_sandbox_snapshot(content=content)
    try:
        path = sample.root / samples.SAMPLE_FILENAME
        assert path.read_bytes() == content
        assert path.stat().st_mode & 0o777 == sample.file_mode == 0o444
        assert samples.observe_sandbox_sample(sample) == 'identity_matches_record'
        assert confirm(sample, mounted_payload(sample))
    finally:
        samples.cleanup_sandbox_sample(sample)
    assert not sample.root.parent.exists() and sample.token not in samples._ACTIVE_SAMPLES


@pytest.mark.parametrize('content', ['text', bytearray(b'x'), None, b'x' * (samples.MAX_SANDBOX_SNAPSHOT_BYTES + 1)])
def test_invalid_input_has_no_filesystem_effect(sample_base, content):
    before = dict(samples._ACTIVE_SAMPLES)
    with pytest.raises((TypeError, samples.SandboxSampleError)):
        samples.create_sandbox_snapshot(content=content)
    assert list(sample_base.iterdir()) == [] and samples._ACTIVE_SAMPLES == before


def test_short_writes_are_completed(sample_base, monkeypatch):
    original = os.write
    monkeypatch.setattr(samples.os, 'write', lambda fd, data: original(fd, data[:2]))
    sample = samples.create_sandbox_snapshot(content=b'abcdefgh')
    assert (sample.root / samples.SAMPLE_FILENAME).read_bytes() == b'abcdefgh'
    samples.cleanup_sandbox_sample(sample)


@pytest.mark.parametrize('failure', ['zero_write', 'write_error', 'chmod', 'close'])
def test_partial_creation_keeps_location_without_registration(sample_base, monkeypatch, failure):
    before = dict(samples._ACTIVE_SAMPLES)
    original_close = os.close
    original_chmod = os.fchmod
    def fail_write(fd, data):
        if failure == 'zero_write':
            return 0
        raise OSError('PRIVATE target path')
    def fail_chmod(fd, mode):
        if mode == 0o444:
            raise OSError('PRIVATE chmod')
        return original_chmod(fd, mode)
    def fail_close(fd):
        is_file = stat.S_ISREG(os.fstat(fd).st_mode)
        original_close(fd)
        # 仅在目标文件实际关闭后丢弃回执，避免误注入祖先目录遍历。
        if is_file:
            raise OSError('PRIVATE close')
    with monkeypatch.context() as patcher:
        if failure in ('zero_write', 'write_error'):
            patcher.setattr(samples.os, 'write', fail_write)
        elif failure == 'chmod':
            patcher.setattr(samples.os, 'fchmod', fail_chmod)
        else:
            patcher.setattr(samples.os, 'close', fail_close)
        with pytest.raises(samples.SandboxSampleCreationUnconfirmed) as caught:
            samples.create_sandbox_snapshot(content=b'content')
    error = caught.value
    assert error.root is not None and error.root.parent.exists()
    assert samples._ACTIVE_SAMPLES == before
    assert 'PRIVATE' not in str(error)
    # 本测试拥有完整私有目录；产品不会根据未登记的路径自动清理。
    path = error.root / samples.SAMPLE_FILENAME
    if path.exists():
        path.unlink()
    error.root.rmdir()
    error.root.parent.rmdir()


def test_changed_snapshot_mode_refused(sample_base):
    sample = samples.create_sandbox_snapshot(content=b'original')
    path = sample.root / samples.SAMPLE_FILENAME
    path.chmod(0o666)
    try:
        assert samples.observe_sandbox_sample(sample) == 'identity_changed'
        with pytest.raises(samples.SandboxSampleError):
            samples.cleanup_sandbox_sample(sample)
        assert path.read_bytes() == b'original'
    finally:
        path.chmod(0o444)
        samples.cleanup_sandbox_sample(sample)
