"""临时文件探测：真实macOS证据与可控设置/回读故障。"""

import ctypes
import json
from dataclasses import FrozenInstanceError, asdict
from types import SimpleNamespace

import pytest

from app.services.workspace.metadata import workspace_xattr_probe as service


@pytest.fixture
def files(monkeypatch):
    original = service.TemporaryFile
    opened = []
    def tracked(**kwargs):
        file = original(**kwargs)
        opened.append(file)
        return file
    monkeypatch.setattr(service, 'TemporaryFile', tracked)
    yield opened
    assert all(file.closed for file in opened)


def test_real_copy_reports_observations_without_values(files):
    result = service.probe_xattr_copy()
    assert len(files) == 2 and all(file.closed for file in files)
    samples = [item for item in result.attributes if item.name.startswith('com.example.agent_probe.')]
    assert len(samples) == 3
    assert all(item.set_accepted and item.readback == 'matched' and not item.matched_before for item in samples)
    assert set(asdict(result.attributes[0])) == {'name', 'matched_before', 'set_accepted', 'readback'}
    with pytest.raises(FrozenInstanceError):
        result.visible_snapshot_equal = False
    # 只输出公开观察字段，绝不打印属性值或摘要。
    print('NATIVE_PROBE=' + json.dumps(asdict(result), ensure_ascii=False))


@pytest.fixture
def harness(files, monkeypatch):
    data = {}
    calls = []
    reads = []
    def set_attribute(fd, name, buffer, size, position, options):
        assert position == 0 and options == 0
        value = ctypes.string_at(buffer, size)
        calls.append((fd, name, value))
        data.setdefault(fd, {})[name] = value
        return 0
    native = SimpleNamespace(fsetxattr=set_attribute)
    def read(fd):
        reads.append(fd)
        return tuple(service.FileXattr(name, value) for name, value in sorted(data.get(fd, {}).items()))
    monkeypatch.setattr(service, '_native_library', lambda: native)
    monkeypatch.setattr(service, 'read_file_xattrs', read)
    return SimpleNamespace(data=data, calls=calls, reads=reads, native=native, read=read, files=files)


def test_binary_empty_and_fixed_calls(harness):
    result = service.probe_xattr_copy()
    assert result.visible_snapshot_equal and result.extra_target_names == ()
    assert len(harness.calls) == 6 and len(harness.reads) == 4
    assert {value for _, _, value in harness.calls} == {b'sample', b'', b'\x00\xff\x01'}


@pytest.mark.parametrize('case', ['rejected-missing', 'accepted-missing', 'accepted-different', 'already-equal', 'extra'])
def test_setting_and_readback_are_independent(harness, monkeypatch, case):
    original = harness.native.fsetxattr
    def set_attribute(fd, name, buffer, size, position, options):
        if fd == harness.files[1].fileno():
            if case == 'rejected-missing':
                return -1
            if case == 'accepted-missing':
                return 0
            if case == 'accepted-different':
                harness.data.setdefault(fd, {})[name] = b'OTHER'
                return 0
            if case == 'already-equal':
                return -1
            if case == 'extra':
                harness.data.setdefault(fd, {})[b'com.example.extra'] = b'PRIVATE'
        return original(fd, name, buffer, size, position, options)
    def read(fd):
        if case == 'already-equal' and len(harness.reads) == 1:
            harness.data[fd] = dict(harness.data[harness.files[0].fileno()])
        return harness.read(fd)
    monkeypatch.setattr(harness.native, 'fsetxattr', set_attribute)
    monkeypatch.setattr(service, 'read_file_xattrs', read)
    result = service.probe_xattr_copy()
    expected = 'missing' if case.endswith('missing') else 'different' if case.endswith('different') else 'matched'
    assert all(item.readback == expected for item in result.attributes)
    assert all(item.set_accepted == (case not in ('rejected-missing', 'already-equal')) for item in result.attributes)
    assert all(item.matched_before == (case == 'already-equal') for item in result.attributes)
    assert result.visible_snapshot_equal == (case == 'already-equal')
    assert result.extra_target_names == (('com.example.extra',) if case == 'extra' else ())
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('phase', ['seed-set', 'seed-read', 'target-before', 'target-after', 'source-after'])
def test_failures_and_cleanup(harness, monkeypatch, phase):
    if phase == 'seed-set':
        monkeypatch.setattr(harness.native, 'fsetxattr', lambda *args: -1)
    else:
        index = {'seed-read': 0, 'target-before': 1, 'target-after': 2, 'source-after': 3}[phase]
        def read(fd):
            if len(harness.reads) == index:
                # 目标回读失败后仍要允许最终源核对继续执行。
                harness.reads.append(fd)
                raise service.FileXattrError('file_xattr_unavailable')
            return harness.read(fd)
        monkeypatch.setattr(service, 'read_file_xattrs', read)
    if phase == 'target-after':
        result = service.probe_xattr_copy()
        assert result.extra_target_names is None and not result.visible_snapshot_equal
        assert all(item.readback == 'unavailable' for item in result.attributes)
    else:
        with pytest.raises((service.FileXattrError, service.XattrProbeError)):
            service.probe_xattr_copy()


@pytest.mark.parametrize('phase', ['seed', 'source-after'])
def test_changed_source_or_unconfirmed_seed_rejected(harness, monkeypatch, phase):
    def read(fd):
        count = len(harness.reads)
        result = harness.read(fd)
        if count == (0 if phase == 'seed' else 3):
            return ()
        return result
    monkeypatch.setattr(service, 'read_file_xattrs', read)
    with pytest.raises(service.XattrProbeError, match='sample_unconfirmed' if phase == 'seed' else 'source_changed'):
        service.probe_xattr_copy()


@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt])
def test_native_exception_closes_both_files(harness, monkeypatch, error_type):
    def fail(*args):
        raise error_type('PRIVATE')
    monkeypatch.setattr(harness.native, 'fsetxattr', fail)
    with pytest.raises(service.XattrProbeError if error_type is OSError else KeyboardInterrupt) as caught:
        service.probe_xattr_copy()
    if error_type is OSError:
        assert 'PRIVATE' not in str(caught.value)


def test_second_file_creation_failure_closes_first(files, monkeypatch):
    original = service.TemporaryFile
    def create(**kwargs):
        if files:
            raise OSError('PRIVATE temporary path')
        return original(**kwargs)
    monkeypatch.setattr(service, 'TemporaryFile', create)
    with pytest.raises(service.XattrProbeError, match='unavailable'):
        service.probe_xattr_copy()
    assert len(files) == 1 and files[0].closed


def test_cleanup_failure_does_not_return_receipt(harness, monkeypatch):
    original = service.TemporaryFile
    class CleanupFailure:
        def __init__(self, file):
            self.file = file
        def __enter__(self):
            return self.file
        def __exit__(self, *args):
            self.file.close()
            raise OSError('PRIVATE cleanup')
    monkeypatch.setattr(service, 'TemporaryFile', lambda **kw: CleanupFailure(original(**kw)))
    with pytest.raises(service.XattrProbeError, match='unavailable'):
        service.probe_xattr_copy()


def test_platform_rejected_before_files(files, monkeypatch):
    service._native_library.cache_clear()
    monkeypatch.setattr(service.sys, 'platform', 'linux')
    with pytest.raises(service.XattrProbeError, match='unsupported'):
        service.probe_xattr_copy()
    assert not files
    service._native_library.cache_clear()
