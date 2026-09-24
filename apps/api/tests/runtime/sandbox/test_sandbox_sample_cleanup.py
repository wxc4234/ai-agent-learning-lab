"""显式清理必须重新核对 Docker 与真实样例，失败保留各自事实。"""

import asyncio
import json
from dataclasses import replace

import pytest

from app.services.runtime.sandbox import sandbox_sample_cleanup as service
from app.services.runtime.sandbox import sandbox_sample as samples
from tests.runtime.sandbox.test_sandbox_creation import CID
from tests.runtime.sandbox.test_sandbox_sample import sample as sample  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample_reconciliation import recovery_for
from tests.runtime.sandbox.test_sandbox_stop import data


def install(monkeypatch, *, status='created', absent=False, failure=None, cancel=False, mutate=None):
    calls = []
    async def query(*, container_id):
        nonlocal absent
        assert container_id == CID
        calls.append('query')
        if failure == 'query':
            raise OSError('PRIVATE')
        return absent
    async def inspect(*, container_id):
        assert container_id == CID
        calls.append('inspect')
        payload = data(status)
        if mutate:
            mutate(payload)
        if failure == 'inspect':
            raise OSError('PRIVATE')
        return json.dumps(payload)
    async def remove(*, container_id):
        nonlocal absent
        assert container_id == CID
        calls.append('remove')
        absent = failure != 'still_present'
        if cancel:
            raise asyncio.CancelledError
        if failure == 'remove':
            raise OSError('PRIVATE response lost')
    monkeypatch.setattr(service, 'is_sandbox_container_absent', query)
    monkeypatch.setattr(service, 'inspect_sandbox_container_by_id', inspect)
    monkeypatch.setattr(service, 'remove_sandbox_container', remove)
    return calls


def run(recovery):
    return asyncio.run(service.cleanup_sample_command(recovery=recovery))


@pytest.mark.parametrize('status', ['created', 'exited'])
def test_stopped_cleanup(monkeypatch, sample, status):
    calls = install(monkeypatch, status=status)
    result = run(recovery_for(sample, execution_reason='timed_out'))
    assert result.recovery.sample_cleaned and result.recovery.container_absent
    assert result.recovery.delete_attempted
    assert result.recovery.execution_reason == 'timed_out'
    assert calls == ['query', 'inspect', 'remove', 'query']
    assert not sample.root.parent.exists() and sample.token not in samples._ACTIVE_SAMPLES


def test_absent_skips_delete_but_rechecks_old_flags(monkeypatch, sample):
    calls = install(monkeypatch, absent=True)
    result = run(recovery_for(sample, container_absent=True, sample_cleaned=True))
    assert result.recovery.sample_cleaned and calls == ['query']
    with pytest.raises(service.SampleCleanupUnconfirmed):
        run(result.recovery)
    assert calls == ['query']


@pytest.mark.parametrize('failure', ['query', 'inspect', 'remove', 'still_present'])
def test_failure_preserves_source_and_does_not_infer_absence(monkeypatch, sample, failure):
    install(monkeypatch, failure=failure)
    with pytest.raises(service.SampleCleanupUnconfirmed) as caught:
        run(recovery_for(sample, container_absent=True))
    assert not caught.value.recovery.container_absent
    assert caught.value.recovery.delete_attempted == (failure in ('remove', 'still_present'))
    assert samples.confirm_sandbox_sample_source(sample)
    assert 'PRIVATE' not in str(caught.value)


def test_running_refused_even_with_stale_stop(monkeypatch, sample):
    calls = install(monkeypatch, status='running')
    with pytest.raises(service.SampleCleanupUnconfirmed):
        run(recovery_for(sample, stop_confirmed=True))
    assert calls == ['query', 'inspect'] and sample.root.exists()


@pytest.mark.parametrize('field,value', [('Id', 'c' * 64), ('Name', '/foreign')])
def test_identity_change_refused(monkeypatch, sample, field, value):
    calls = install(monkeypatch, mutate=lambda payload: payload[0].update({field: value}))
    with pytest.raises(service.SampleCleanupUnconfirmed):
        run(recovery_for(sample))
    assert 'remove' not in calls and sample.root.exists()


@pytest.mark.parametrize('change', ['unknown_id', 'partial', 'copy', 'changed'])
def test_invalid_source_or_unknown_id_never_touches_docker(monkeypatch, sample, change):
    calls = install(monkeypatch)
    recovery = recovery_for(sample)
    if change == 'unknown_id':
        recovery = replace(recovery, container_id=None, phase='creating')
    elif change == 'partial':
        recovery = replace(recovery, sample=None)
    elif change == 'copy':
        recovery = replace(recovery, sample=replace(sample))
    else:
        (sample.root / 'extra').write_text('x')
    try:
        with pytest.raises(service.SampleCleanupUnconfirmed):
            run(recovery)
        assert calls == []
    finally:
        if change == 'changed':
            (sample.root / 'extra').unlink()


def test_cancel_after_delete_keeps_source_then_explicit_retry(monkeypatch, sample):
    calls = install(monkeypatch, cancel=True)
    with pytest.raises(service.SampleCleanupCancelled) as caught:
        run(recovery_for(sample))
    assert caught.value.recovery.delete_attempted and not caught.value.recovery.container_absent
    assert sample.root.exists() and not service._CLEANING
    result = run(caught.value.recovery)
    assert result.recovery.sample_cleaned
    assert calls.count('remove') == 1


def test_source_failure_after_absence_preserves_container_fact(monkeypatch, sample):
    install(monkeypatch, absent=True)
    original = service.cleanup_sandbox_sample
    def fail(_sample):
        raise OSError('PRIVATE')
    monkeypatch.setattr(service, 'cleanup_sandbox_sample', fail)
    with pytest.raises(service.SampleCleanupUnconfirmed) as caught:
        run(recovery_for(sample))
    assert caught.value.recovery.container_absent and not caught.value.recovery.sample_cleaned
    assert sample.root.exists()
    monkeypatch.setattr(service, 'cleanup_sandbox_sample', original)


def test_concurrent_cleanup_rejected_and_cancel_releases_guard(monkeypatch, sample):
    async def scenario():
        entered = asyncio.Event()
        async def blocked(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(service, 'is_sandbox_container_absent', blocked)
        recovery = recovery_for(sample)
        task = asyncio.create_task(service.cleanup_sample_command(recovery=recovery))
        await entered.wait()
        with pytest.raises(service.SampleCleanupUnconfirmed):
            await service.cleanup_sample_command(recovery=recovery)
        task.cancel()
        with pytest.raises(service.SampleCleanupCancelled):
            await task
        assert not service._CLEANING and sample.root.exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('boundary', ['inspect', 'absence'])
def test_source_changed_during_docker_wait_is_not_released(monkeypatch, sample, boundary):
    calls = install(monkeypatch)
    name = 'inspect_sandbox_container_by_id' if boundary == 'inspect' else 'is_sandbox_container_absent'
    original = getattr(service, name)
    count = 0
    async def changed(**kwargs):
        nonlocal count
        result = await original(**kwargs)
        count += 1
        if boundary == 'inspect' or count == 2:
            (sample.root / 'extra').write_text('changed')
        return result
    monkeypatch.setattr(service, name, changed)
    try:
        with pytest.raises(service.SampleCleanupUnconfirmed) as caught:
            run(recovery_for(sample))
        assert caught.value.recovery.container_absent == (boundary == 'absence')
        assert ('remove' in calls) == (boundary == 'absence')
        assert (sample.root / samples.SAMPLE_FILENAME).exists()
    finally:
        (sample.root / 'extra').unlink()


def test_partial_filesystem_cleanup_is_not_retried_blindly(monkeypatch, sample):
    calls = install(monkeypatch, absent=True)
    original = samples.os.rmdir
    def fail(*args, **kwargs):
        raise OSError('injected rmdir failure')
    with monkeypatch.context() as patcher:
        patcher.setattr(samples.os, 'rmdir', fail)
        with pytest.raises(service.SampleCleanupUnconfirmed) as caught:
            run(recovery_for(sample))
    assert caught.value.recovery.container_absent
    with pytest.raises(service.SampleCleanupUnconfirmed):
        run(caught.value.recovery)
    assert calls == ['query']
    # 只有测试拥有者可收尾故意破坏的私有现场；产品入口不能凭路径递归删除。
    samples._ACTIVE_SAMPLES.pop(sample.token)
    original(sample.root)
    original(sample.root.parent)
