"""真实临时样例、进程内登记失效及多线程借用关闭边界。"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.services.workspace.samples import temporary_sample_registry as m
from app.services.workspace.samples import temporary_proposal_sample as lifecycle


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle.tempfile, 'gettempdir', lambda: str(tmp_path))
    return m.TemporarySampleRegistry()


def test_create_borrow_close_and_stale_handle(registry, tmp_path):
    handle = registry.create()
    assert handle.token not in repr(handle)
    entry = registry._entries[handle.token]
    assert entry.parent_identity == entry.sample.parent_identity
    assert entry.root_identity == entry.sample.root_identity
    with registry.borrow(handle) as sample:
        root = sample.root
        assert (root / sample.relative_path).read_bytes() == b'old\n'
    with registry.borrow(handle) as again:
        assert again.root == root
    assert registry.close(handle) is True
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
        pytest.fail('stale')
    with pytest.raises(m.SampleRegistryError):
        registry.close(handle)


@pytest.mark.parametrize('handle', [None, 'arbitrary', m.SampleHandle('unknown'), m.SampleHandle(123)])
def test_unknown_handles_never_create_or_touch_paths(registry, tmp_path, handle):
    with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
        pytest.fail('unknown')
    with pytest.raises(m.SampleRegistryError):
        registry.close(handle)
    assert list(tmp_path.iterdir()) == []


def test_foreign_registry_and_restart_do_not_restore(registry):
    handle = registry.create()
    other = m.TemporarySampleRegistry()
    with pytest.raises(m.SampleRegistryError), other.borrow(handle):
        pytest.fail('foreign')
    assert registry.close(handle)


def test_exclusive_borrow_and_close_from_same_thread(registry):
    handle = registry.create()
    with registry.borrow(handle) as sample:
        with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
            pytest.fail('concurrent')
        assert registry.close(handle) is False
        assert registry.close(handle) is False
        assert sample.root.exists()
        with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
            pytest.fail('closing')
    assert not sample.root.exists()


def test_thread_close_revokes_without_cleaning_active_borrow(registry):
    handle = registry.create()
    entered, release = Event(), Event()
    def worker():
        with registry.borrow(handle) as sample:
            entered.set()
            assert release.wait(5)
            assert sample.root.exists()
            return sample.root
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(worker)
        try:
            assert entered.wait(5)
            assert registry.close(handle) is False
            with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
                pytest.fail('closing')
        finally:
            release.set()
        root = job.result(timeout=5)
    assert not root.exists()


@pytest.mark.parametrize('error', [ValueError('body'), KeyboardInterrupt()])
def test_failed_use_revokes_and_cleans(registry, tmp_path, error):
    handle = registry.create()
    with pytest.raises(type(error)) as caught, registry.borrow(handle):
        raise error
    assert caught.value is error
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
        pytest.fail('failed lease')


def test_failed_cleanup_stays_invalid_and_no_retry(registry, monkeypatch):
    handle = registry.create()
    with registry.borrow(handle) as sample:
        (sample.root / 'unknown').write_bytes(b'keep')
    with pytest.raises(lifecycle.TemporarySampleError):
        registry.close(handle)
    with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
        pytest.fail('failed cleanup')
    with pytest.raises(m.SampleRegistryError):
        registry.close(handle)
    assert sample.root.exists()
    # 清理失败仍占容量，不能无限创建遗留现场。
    monkeypatch.setattr(m, 'MAX_REGISTERED_SAMPLES', 1)
    with pytest.raises(m.SampleRegistryError, match='capacity'):
        registry.create()


def test_body_error_survives_failed_deferred_cleanup(registry):
    handle = registry.create()
    error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught, registry.borrow(handle) as sample:
        (sample.root / 'unknown').write_bytes(b'keep')
        assert registry.close(handle) is False
        raise error
    assert caught.value is error and 'sample_cleanup_incomplete' in error.__notes__
    with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
        pytest.fail('invalid')


def test_changed_root_rejected_without_deleting_replacement(registry):
    handle = registry.create()
    with registry.borrow(handle) as sample:
        moved = sample.root.with_name('moved')
        sample.root.rename(moved)
        sample.root.mkdir()
        (sample.root / 'private').write_bytes(b'keep')
    with pytest.raises(m.SampleRegistryError), registry.borrow(handle):
        pytest.fail('changed')
    assert (sample.root / 'private').read_bytes() == b'keep'
    assert (moved / sample.relative_path).read_bytes() == b'old\n'


def test_forked_pid_refused_before_lock_or_cleanup(registry, monkeypatch):
    handle = registry.create()
    pid = os.getpid()
    with monkeypatch.context() as patch:
        patch.setattr(m.os, 'getpid', lambda: pid + 1)
        with pytest.raises(m.SampleRegistryError, match='process_changed'):
            registry.create()
        with pytest.raises(m.SampleRegistryError, match='process_changed'):
            registry.close(handle)
        with pytest.raises(m.SampleRegistryError, match='process_changed'), registry.borrow(handle):
            pytest.fail('fork')
    assert registry.close(handle)


def test_capacity_and_token_collision_do_not_create_extra_directories(registry, tmp_path, monkeypatch):
    handle = registry.create()
    monkeypatch.setattr(m.secrets, 'token_hex', lambda size: handle.token)
    with pytest.raises(m.SampleRegistryError):
        registry.create()
    assert len(list(tmp_path.iterdir())) == 1
    monkeypatch.setattr(m, 'MAX_REGISTERED_SAMPLES', 1)
    with pytest.raises(m.SampleRegistryError, match='capacity'):
        registry.create()
    assert registry.close(handle)


def test_creation_failure_does_not_publish_entry(registry, monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def fail():
        raise lifecycle.TemporarySampleError('sample_creation_failed')
        yield  # pragma: no cover
    monkeypatch.setattr(m, 'temporary_proposal_sample', fail)
    with pytest.raises(lifecycle.TemporarySampleError):
        registry.create()
    assert registry._entries == {}


def test_identity_capture_failure_cleans_unpublished_sample(registry, tmp_path, monkeypatch):
    def fail(path):
        raise OSError('identity capture')
    monkeypatch.setattr(registry, '_identity', fail)
    with pytest.raises(OSError):
        registry.create()
    assert registry._entries == {} and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('changed', ['root', 'parent'])
def test_identity_mismatch_rejects_registration_and_cleans_unpublished_sample(
    registry, tmp_path, monkeypatch, changed,
):
    def mismatched_identity(path):
        info = path.stat(follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (path == tmp_path) == (changed == 'parent'):
            return identity[0], identity[1] + 1
        return identity

    monkeypatch.setattr(registry, '_identity', mismatched_identity)
    with pytest.raises(m.SampleRegistryError, match='sample_registration_unavailable'):
        registry.create()
    assert registry._entries == {}
    assert list(tmp_path.iterdir()) == []


def test_missing_root_invalidates_registration(registry):
    handle = registry.create()
    with registry.borrow(handle) as sample:
        (sample.root / sample.relative_path).unlink()
        sample.root.rmdir()
    with pytest.raises(m.SampleRegistryError) as caught, registry.borrow(handle):
        pytest.fail('missing')
    assert 'sample_cleanup_incomplete' in caught.value.__notes__
    with pytest.raises(m.SampleRegistryError):
        registry.close(handle)
