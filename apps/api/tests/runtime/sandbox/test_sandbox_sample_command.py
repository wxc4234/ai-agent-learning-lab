"""独占样例编排、阶段证据、取消和源生命周期；Docker 由有界 stub 替代。"""

import asyncio
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.services.runtime.command.command_contracts import CommandRequest
from app.services.runtime.sandbox import sandbox_sample_command as service
from app.services.runtime.sandbox import sandbox_sample as samples
from tests.runtime.sandbox.test_sandbox_creation import CID, request
from tests.runtime.sandbox.test_sandbox_command_result import execution
from tests.runtime.sandbox.test_sandbox_sample import mounted_payload
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414 -- pytest fixture export.
from tests.runtime.sandbox.test_sandbox_stop import data as state_payload


@pytest.fixture
def lab(monkeypatch, sample_base):
    value = SimpleNamespace(calls=[], samples=[], spec=None, state='created', inspections=0,
                            failure=None, error=None, code=0, mutate=None, absence=True,
                            cleanup_identity_bad=False, cleanup_running=False)
    real_create = service.create_sandbox_sample
    real_cleanup = service.cleanup_sandbox_sample

    def fail(stage):
        if value.failure == stage:
            raise value.error or OSError('PRIVATE detail')

    def create_sample():
        value.calls.append('prepare')
        fail('prepare')
        sample = real_create()
        value.samples.append(sample)
        return sample

    async def create_container(*, spec):
        value.calls.append('create')
        value.spec = spec
        if value.mutate:
            value.mutate()
        fail('create')
        return CID + '\n'

    async def inspect(*, container_id):
        assert container_id == CID
        value.inspections += 1
        phase = 'inspect_create' if value.inspections == 1 else 'inspect_cleanup'
        value.calls.append(phase)
        fail(phase)
        payload = mounted_payload(value.samples[-1])
        payload[0]['Name'] = '/' + value.spec.container_name
        payload[0]['Config']['Labels']['ai-agent-learning-lab.execution'] = value.spec.execution_token
        payload[0]['State'] = state_payload(value.state)[0]['State']
        if value.inspections > 1:
            if value.cleanup_identity_bad:
                payload[0]['Id'] = 'f' * 64
            if value.cleanup_running:
                payload[0]['State'] = state_payload('running')[0]['State']
        return json.dumps(payload)

    async def execute(**kwargs):
        value.calls.append('execute')
        assert kwargs['request'].argv == request().argv
        assert kwargs['sample'] is value.samples[-1]
        assert kwargs['execution_token'] == value.spec.execution_token
        assert kwargs['expected_container_id'] == CID
        fail('execute')
        value.state = 'exited'
        return execution(code=value.code)

    async def remove(*, container_id):
        value.calls.append('remove')
        assert container_id == CID and value.samples[-1].root.exists()
        fail('remove')

    async def absent(*, container_id):
        value.calls.append('absent')
        assert container_id == CID and value.samples[-1].root.exists()
        fail('absent')
        return value.absence

    def cleanup(sample):
        value.calls.append('cleanup_sample')
        assert value.calls[-2] == 'absent'
        fail('cleanup_sample')
        real_cleanup(sample)

    monkeypatch.setattr(service, 'create_sandbox_sample', create_sample)
    monkeypatch.setattr(service, 'create_sandbox_container', create_container)
    monkeypatch.setattr(service, 'inspect_sandbox_container_by_id', inspect)
    monkeypatch.setattr(service, 'execute_created_sandbox', execute)
    monkeypatch.setattr(service, 'remove_sandbox_container', remove)
    monkeypatch.setattr(service, 'is_sandbox_container_absent', absent)
    monkeypatch.setattr(service, 'cleanup_sandbox_sample', cleanup)
    yield value
    for sample in value.samples:
        if samples._ACTIVE_SAMPLES.get(sample.token) is sample:
            real_cleanup(sample)


@pytest.mark.parametrize('code', [0, 7, 137])
def test_success_and_nonzero_preserve_result_and_order(lab, code):
    lab.code = code
    result = asyncio.run(service.run_sample_sandbox_command(request=request()))
    assert lab.calls == ['prepare', 'create', 'inspect_create', 'execute', 'inspect_cleanup',
                         'remove', 'absent', 'cleanup_sample']
    assert result.command.exit_code == code
    assert result.command.succeeded is (code == 0)
    assert result.command.stdout == '你好\n'
    assert result.cleanup.container_id == CID
    assert result.sample_cleaned and not lab.samples[-1].root.parent.exists()
    with pytest.raises(FrozenInstanceError):
        result.sample_cleaned = False


@pytest.mark.parametrize('stage,phase', [
    ('prepare', 'preparing'), ('create', 'creating'), ('inspect_create', 'creating'),
    ('execute', 'executing'), ('inspect_cleanup', 'cleaning_container'),
    ('remove', 'cleaning_container'), ('absent', 'cleaning_container'),
    ('cleanup_sample', 'cleaning_sample'),
])
@pytest.mark.parametrize('cancel', [False, True])
def test_failures_keep_source_and_known_evidence(lab, stage, phase, cancel):
    lab.failure = stage
    lab.error = asyncio.CancelledError('PRIVATE') if cancel else OSError('PRIVATE')
    error_type = service.SampleCommandCancelled if cancel else service.SampleCommandUnconfirmed
    with pytest.raises(error_type) as caught:
        asyncio.run(service.run_sample_sandbox_command(request=request()))
    recovery = caught.value.recovery
    assert recovery.phase == phase
    assert 'PRIVATE' not in str(caught.value)
    assert recovery.create_attempted is (stage != 'prepare')
    assert (recovery.container_id == CID) is (stage not in ('prepare', 'create'))
    assert recovery.delete_attempted is (stage in ('remove', 'absent', 'cleanup_sample'))
    assert recovery.container_absent is (stage == 'cleanup_sample')
    assert not recovery.sample_cleaned
    assert (recovery.command is not None) is (stage in ('inspect_cleanup', 'remove', 'absent', 'cleanup_sample'))
    assert recovery.build_request().argv == request().argv
    if stage != 'prepare':
        assert recovery.sample is lab.samples[-1]
        assert recovery.sample_root == lab.samples[-1].root
        assert recovery.sample_root.exists()
    assert lab.calls.count('create') <= 1
    with pytest.raises(FrozenInstanceError):
        recovery.phase = 'preparing'


@pytest.mark.parametrize('start,stop', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('reason', ['timed_out', 'execution_failed', 'cancelled'])
def test_execution_failure_keeps_stop_facts_without_deleting(lab, start, stop, reason):
    facts = {'execution_token': 'b' * 32, 'container_id': CID,
             'start_attempted': start, 'stop_confirmed': stop}
    lab.failure = 'execute'
    lab.error = (service.SandboxExecutionCancelled(**facts) if reason == 'cancelled'
                 else service.SandboxExecutionUnconfirmed(**facts, reason=reason))
    with pytest.raises(service.SampleCommandCancelled if reason == 'cancelled'
                       else service.SampleCommandUnconfirmed) as caught:
        asyncio.run(service.run_sample_sandbox_command(request=request()))
    recovery = caught.value.recovery
    assert recovery.start_attempted is start and recovery.stop_confirmed is stop
    assert recovery.execution_reason == (None if reason == 'cancelled' else reason)
    assert recovery.command is None and not recovery.delete_attempted
    assert recovery.sample.root.exists()
    assert 'remove' not in lab.calls and 'cleanup_sample' not in lab.calls


@pytest.mark.parametrize('failure', ['identity', 'running', 'not_absent'])
def test_cleanup_requires_fresh_identity_exited_and_absence(lab, failure):
    lab.cleanup_identity_bad = failure == 'identity'
    lab.cleanup_running = failure == 'running'
    lab.absence = failure != 'not_absent'
    with pytest.raises(service.SampleCommandUnconfirmed) as caught:
        asyncio.run(service.run_sample_sandbox_command(request=request()))
    assert caught.value.recovery.command.succeeded
    assert not caught.value.recovery.container_absent
    assert 'cleanup_sample' not in lab.calls
    assert ('remove' in lab.calls) is (failure == 'not_absent')


def test_caller_mutation_cannot_change_frozen_request(lab):
    original = request()
    lab.mutate = lambda: original.argv.clear()
    result = asyncio.run(service.run_sample_sandbox_command(request=original))
    assert result.command.succeeded and original.argv == []


@pytest.mark.parametrize('invalid', [None, {}, CommandRequest(argv=['relative']),
                                    CommandRequest(argv=['/bin/true'], working_directory='subdir')])
def test_validation_before_sample_or_docker(lab, invalid):
    with pytest.raises((ValueError, TypeError)):
        asyncio.run(service.run_sample_sandbox_command(request=invalid))
    assert lab.calls == []


def test_spec_recheck_failure_keeps_precreate_source(lab, monkeypatch):
    def fail(**kwargs):
        raise samples.SandboxSampleError()
    monkeypatch.setattr(service, 'build_sample_sandbox_create_spec', fail)
    with pytest.raises(service.SampleCommandUnconfirmed) as caught:
        asyncio.run(service.run_sample_sandbox_command(request=request()))
    assert not caught.value.recovery.create_attempted
    assert caught.value.recovery.sample.root.exists()
    assert lab.calls == ['prepare']


def test_partial_sample_creation_preserves_owned_site(sample_base, monkeypatch):
    def fail_write(*args):
        raise OSError('PRIVATE')
    monkeypatch.setattr(samples.os, 'write', fail_write)
    with pytest.raises(service.SampleCommandUnconfirmed) as caught:
        asyncio.run(service.run_sample_sandbox_command(request=request()))
    recovery = caught.value.recovery
    assert recovery.phase == 'preparing' and not recovery.create_attempted
    assert recovery.sample is None and recovery.sample_token is not None
    assert recovery.sample_root.parent.exists()
    # 私有 pytest 目录中的故障现场由用例显式核对后清理。
    assert recovery.sample_root.parent.parent == sample_base
    (recovery.sample_root / samples.SAMPLE_FILENAME).unlink()
    recovery.sample_root.rmdir()
    recovery.sample_root.parent.rmdir()


@pytest.mark.parametrize('stage', ['create', 'execute', 'remove', 'absent'])
def test_actual_task_cancel_waits_for_operation_finally(lab, monkeypatch, stage):
    async def scenario():
        entered, settled = asyncio.Event(), asyncio.Event()
        async def blocked(**kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                settled.set()
        name = {'create': 'create_sandbox_container', 'execute': 'execute_created_sandbox',
                'remove': 'remove_sandbox_container', 'absent': 'is_sandbox_container_absent'}[stage]
        monkeypatch.setattr(service, name, blocked)
        task = asyncio.create_task(service.run_sample_sandbox_command(request=request()))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(service.SampleCommandCancelled) as caught:
                await task
            assert settled.is_set() and task.cancelled()
            assert caught.value.recovery.sample.root.exists()
            assert not caught.value.recovery.sample_cleaned
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 2))


def test_factory_collision_does_not_claim_existing_directory(sample_base, monkeypatch):
    token = 'a' * 32
    existing = sample_base / f'agent-sandbox-sample-{token}'
    existing.mkdir()
    sentinel = existing / 'keep.txt'
    sentinel.write_bytes(b'existing owner')
    monkeypatch.setattr(samples, 'uuid4', lambda: SimpleNamespace(hex=token))
    try:
        with pytest.raises(service.SampleCommandUnconfirmed) as caught:
            asyncio.run(service.run_sample_sandbox_command(request=request()))
        assert caught.value.recovery.sample_token == token
        assert caught.value.recovery.sample_root is None
        assert caught.value.recovery.sample is None
        assert not caught.value.recovery.create_attempted
        assert sentinel.read_bytes() == b'existing owner'
    finally:
        sentinel.unlink()
        existing.rmdir()


def test_result_adaptation_failure_retains_stop_without_fabricating_command(lab, monkeypatch):
    def fail(execution):
        raise ValueError('PRIVATE adaptation')
    monkeypatch.setattr(service, 'build_command_result', fail)
    with pytest.raises(service.SampleCommandUnconfirmed) as caught:
        asyncio.run(service.run_sample_sandbox_command(request=request()))
    recovery = caught.value.recovery
    assert recovery.phase == 'adapting'
    assert recovery.start_attempted and recovery.stop_confirmed
    assert recovery.command is None and not recovery.delete_attempted
    assert recovery.sample.root.exists() and 'remove' not in lab.calls


def test_invalid_create_receipt_keeps_token_and_source(lab, monkeypatch):
    async def create(*, spec):
        return 'not-a-complete-container-id\n'
    monkeypatch.setattr(service, 'create_sandbox_container', create)
    with pytest.raises(service.SampleCommandUnconfirmed) as caught:
        asyncio.run(service.run_sample_sandbox_command(request=request()))
    recovery = caught.value.recovery
    assert recovery.create_attempted and recovery.container_id is None
    assert len(recovery.execution_token) == 32
    assert recovery.sample.root.exists()
    assert 'inspect_create' not in lab.calls and 'remove' not in lab.calls
