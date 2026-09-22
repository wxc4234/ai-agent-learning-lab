"""生命周期只操作自有临时目录，故障保留现场由测试tmp_path清理。"""

import os
import stat
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.services.workspace.samples import temporary_proposal_sample as service


@pytest.fixture
def parent(tmp_path, monkeypatch):
    monkeypatch.setattr(service.tempfile, 'gettempdir', lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def descriptors(monkeypatch):
    opened = []
    native = os.open
    def tracked(*args, **kwargs):
        descriptor = native(*args, **kwargs)
        opened.append(descriptor)
        return descriptor
    monkeypatch.setattr(service.os, 'open', tracked)
    yield opened
    for descriptor in set(opened):
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_fixed_private_sample_and_cleanup(parent, descriptors):
    with service.temporary_proposal_sample() as sample:
        root = sample.root
        assert root.parent == parent
        assert sample.relative_path == 'example.txt'
        assert (root / sample.relative_path).read_bytes() == b'old\n'
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE((root / sample.relative_path).stat().st_mode) == 0o600
        assert str(root) not in repr(sample)
        with pytest.raises(FrozenInstanceError):
            sample.relative_path = 'other'
    assert not root.exists() and list(parent.iterdir()) == []


def test_nested_scopes_are_independent(parent, descriptors):
    with service.temporary_proposal_sample() as first:
        with service.temporary_proposal_sample() as second:
            assert first.root != second.root
        assert first.root.exists() and not second.root.exists()
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize('error', [ValueError('body'), OSError('body'), KeyboardInterrupt()])
def test_body_failure_preserved_and_cleaned(parent, descriptors, error):
    with pytest.raises(type(error)) as caught, service.temporary_proposal_sample():
        raise error
    assert caught.value is error
    assert list(parent.iterdir()) == []


def test_legitimate_atomic_replacement_is_cleaned(parent, descriptors):
    with service.temporary_proposal_sample() as sample:
        staged = sample.root / 'staged'
        staged.write_bytes(b'new\n')
        os.replace(staged, sample.root / sample.relative_path)
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize('kind', ['extra', 'directory', 'symlink', 'hardlink'])
def test_unknown_or_unsafe_entries_preserved(parent, descriptors, kind):
    outside = parent / 'outside'
    outside.write_bytes(b'private')
    with pytest.raises(service.TemporarySampleError, match='cleanup_incomplete'), service.temporary_proposal_sample() as sample:
        file = sample.root / sample.relative_path
        if kind == 'extra':
            (sample.root / 'unknown').write_bytes(b'keep')
        else:
            file.unlink()
            if kind == 'directory':
                file.mkdir()
            elif kind == 'symlink':
                file.symlink_to(outside)
            else:
                os.link(outside, file)
    assert sample.root.exists()
    assert outside.read_bytes() == b'private'
    assert (sample.root / sample.relative_path).exists()


def test_root_replaced_is_not_deleted(parent, descriptors):
    with pytest.raises(service.TemporarySampleError, match='cleanup_incomplete'), service.temporary_proposal_sample() as sample:
        moved = parent / 'moved'
        sample.root.rename(moved)
        sample.root.mkdir()
        (sample.root / 'foreign').write_bytes(b'keep')
    assert (sample.root / 'foreign').read_bytes() == b'keep'
    assert (moved / sample.relative_path).read_bytes() == b'old\n'


def test_cleanup_failure_does_not_mask_body_error(parent, descriptors):
    error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught, service.temporary_proposal_sample() as sample:
        (sample.root / 'unknown').write_bytes(b'keep')
        raise error
    assert caught.value is error
    assert error.__notes__ == ['sample_cleanup_incomplete']
    assert sample.root.exists()


def test_collision_never_cleans_preexisting_directory(parent, descriptors, monkeypatch):
    monkeypatch.setattr(service, 'uuid4', lambda: SimpleNamespace(hex='fixed'))
    existing = parent / 'agent-proposal-fixed'
    existing.mkdir()
    (existing / 'keep').write_bytes(b'keep')
    with pytest.raises(service.TemporarySampleError, match='creation_failed'), service.temporary_proposal_sample():
        pytest.fail('must not yield')
    assert (existing / 'keep').read_bytes() == b'keep'


@pytest.mark.parametrize('zero', [False, True])
def test_short_or_zero_write(parent, descriptors, monkeypatch, zero):
    native = os.write
    monkeypatch.setattr(service.os, 'write', lambda fd, data: 0 if zero else native(fd, data[:1]))
    if zero:
        with pytest.raises(service.TemporarySampleError, match='creation_failed'), service.temporary_proposal_sample():
            pytest.fail('must not yield')
    else:
        with service.temporary_proposal_sample() as sample:
            assert (sample.root / sample.relative_path).read_bytes() == b'old\n'
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize('failure', ['directory_open', 'file_open'])
def test_setup_failure_cleans_owned_resources(parent, descriptors, monkeypatch, failure):
    native = os.open
    def fail(path, *args, **kwargs):
        if (failure == 'directory_open' and str(path).startswith('agent-proposal-')) or path == 'example.txt':
            raise OSError('private path')
        return native(path, *args, **kwargs)
    monkeypatch.setattr(service.os, 'open', fail)
    with pytest.raises(service.TemporarySampleError, match='creation_failed'), service.temporary_proposal_sample():
        pytest.fail('must not yield')
    assert list(parent.iterdir()) == []


def test_platform_rejected_before_creation(parent, monkeypatch):
    monkeypatch.setattr(service.sys, 'platform', 'linux')
    with pytest.raises(service.TemporarySampleError, match='platform_unsupported'), service.temporary_proposal_sample():
        pytest.fail('must not yield')
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize('operation', ['unlink', 'rmdir'])
def test_cleanup_syscall_failure_is_reported(parent, descriptors, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise OSError('private cleanup path')
    with pytest.raises(service.TemporarySampleError, match='cleanup_incomplete'), service.temporary_proposal_sample() as sample:
        monkeypatch.setattr(service.os, operation, fail)
    assert sample.root.exists()
    monkeypatch.undo()


def test_changed_permissions_preserve_directory(parent, descriptors):
    with pytest.raises(service.TemporarySampleError, match='cleanup_incomplete'), service.temporary_proposal_sample() as sample:
        os.chmod(sample.root, 0o755)
    assert (sample.root / sample.relative_path).read_bytes() == b'old\n'
