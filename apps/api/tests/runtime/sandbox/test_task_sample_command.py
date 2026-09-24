"""快照生命周期复用与准备线程取消收尾；Docker 使用阶段替身。"""

import asyncio
from threading import Event

import pytest

from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.sandbox import task_sample_command as service
from tests.runtime.sandbox.test_sandbox_creation import request
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample_command import lab as lab  # noqa: PLC0414


@pytest.fixture
def factory(lab, monkeypatch):
    def prepare(**kwargs):
        assert kwargs == {'user_id': 1, 'conversation_id': 'conversation', 'bindings': None}
        sample = samples.create_sandbox_snapshot(content=b'task content\n')
        lab.samples.append(sample)
        return sample
    monkeypatch.setattr(service, 'create_task_sandbox_snapshot', prepare)
    return prepare


def run():
    return service.run_task_sample_command(
        request=request(), user_id=1, conversation_id='conversation', bindings=None,
    )


@pytest.mark.parametrize('code', [0, 7])
def test_success_and_nonzero_use_snapshot(lab, factory, code):
    lab.code = code
    result = asyncio.run(run())
    assert result.command.exit_code == code and result.sample_cleaned
    assert lab.samples[0].file_mode == 0o444
    assert not lab.samples[0].root.parent.exists()
    assert lab.calls == ['create', 'inspect_create', 'execute', 'inspect_cleanup', 'remove', 'absent', 'cleanup_sample']


@pytest.mark.parametrize('stage', ['create', 'inspect_create', 'execute', 'inspect_cleanup', 'remove', 'absent', 'cleanup_sample'])
@pytest.mark.parametrize('cancel', [False, True])
def test_failures_return_snapshot_and_stage_evidence(lab, factory, stage, cancel):
    lab.failure = stage
    lab.error = asyncio.CancelledError() if cancel else OSError('PRIVATE')
    error_type = owner.SampleCommandCancelled if cancel else owner.SampleCommandUnconfirmed
    with pytest.raises(error_type) as caught:
        asyncio.run(run())
    recovery = caught.value.recovery
    assert recovery.sample is lab.samples[0] and recovery.sample.root.exists()
    assert (recovery.sample.root / 'example.txt').read_bytes() == b'task content\n'
    assert recovery.create_attempted and not recovery.sample_cleaned
    assert (recovery.container_id is None) == (stage == 'create')
    assert (recovery.command is not None) == (stage in ('inspect_cleanup', 'remove', 'absent', 'cleanup_sample'))
    assert recovery.container_absent == (stage == 'cleanup_sample')


@pytest.mark.parametrize('outcome', ['snapshot', 'partial', 'failed'])
def test_repeated_cancel_waits_for_preparation_and_keeps_evidence(lab, monkeypatch, sample_base, outcome):
    entered = Event()
    release = Event()
    completed = Event()
    def prepare(**kwargs):
        entered.set()
        assert release.wait(5)
        try:
            if outcome == 'partial':
                root = sample_base / 'partial'
                root.mkdir()
                raise samples.SandboxSampleCreationUnconfirmed(token='partial', root=root)
            if outcome == 'failed':
                raise OSError('PRIVATE preparation')
            sample = samples.create_sandbox_snapshot(content=b'created after cancellation')
            lab.samples.append(sample)
            return sample
        finally:
            completed.set()
    monkeypatch.setattr(service, 'create_task_sandbox_snapshot', prepare)
    async def scenario():
        task = asyncio.create_task(run())
        try:
            async def wait_entered():
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(wait_entered(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(owner.SampleCommandCancelled) as caught:
            await task
        assert completed.is_set() and task.cancelled()
        recovery = caught.value.recovery
        assert recovery.phase == 'preparing' and not recovery.create_attempted
        assert lab.calls == []
        if outcome == 'snapshot':
            assert recovery.sample is lab.samples[0] and recovery.sample.root.exists()
        elif outcome == 'partial':
            assert recovery.sample is None and recovery.sample_root == sample_base / 'partial'
            assert recovery.sample_token == 'partial'
        else:
            assert recovery.sample is None and recovery.sample_root is None
    asyncio.run(scenario())


def test_invalid_command_never_prepares(lab, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail('must validate before preparation')
    monkeypatch.setattr(service, 'create_task_sandbox_snapshot', forbidden)
    invalid = request()
    invalid.argv = ['relative-command']
    with pytest.raises(ValueError):
        asyncio.run(service.run_task_sample_command(
            request=invalid, user_id=1, conversation_id='conversation', bindings=None,
        ))
    assert lab.calls == []


@pytest.mark.parametrize('partial', [False, True])
def test_preparation_failure_preserves_partial_location(lab, monkeypatch, sample_base, partial):
    def fail(**kwargs):
        if partial:
            raise samples.SandboxSampleCreationUnconfirmed(token='owned', root=sample_base / 'partial')
        raise ValueError('PRIVATE authorization')
    monkeypatch.setattr(service, 'create_task_sandbox_snapshot', fail)
    with pytest.raises(owner.SampleCommandUnconfirmed) as caught:
        asyncio.run(run())
    recovery = caught.value.recovery
    assert not recovery.create_attempted and recovery.phase == 'preparing'
    assert (recovery.sample_root is not None) == partial
    assert lab.calls == [] and 'PRIVATE' not in str(caught.value)
