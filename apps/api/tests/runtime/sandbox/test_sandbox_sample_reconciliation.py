"""只读诊断的分侧未知、严格身份和无副作用；使用真实临时样例。"""

import asyncio
import errno
import json
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.runtime.docker import docker_client as client
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import sandbox_sample_reconciliation as service
from app.services.runtime.sandbox.sandbox_sample_command import SampleCommandRecovery
from tests.runtime.sandbox.test_sandbox_creation import CID, TOKEN, request
from tests.runtime.sandbox.test_sandbox_sample import sample as sample  # noqa: PLC0414 -- pytest fixture export.
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414 -- pytest fixture export.
from tests.runtime.sandbox.test_sandbox_stop import data


def recovery_for(sample, **changes):
    return replace(SampleCommandRecovery(
        execution_token=TOKEN, container_name=f'agent-sandbox-{TOKEN}',
        argv=tuple(request().argv), working_directory='.', phase='executing',
        sample=sample, sample_token=sample.token, sample_root=sample.root,
        container_id=CID, create_attempted=True,
    ), **changes)


def install(monkeypatch, *, status='created', absent=False, failure=None, mutate=None):
    calls = []
    async def runner(arguments):
        calls.append(arguments)
        # CLI 底座白名单，任何生命周期写操作都使测试失败。
        assert arguments[:2] in (('container', 'ls'), ('container', 'inspect'))
        if arguments[1] == failure:
            raise PermissionError('PRIVATE socket')
        payload = data(status)
        if mutate:
            mutate(payload)
        text = ('' if absent else CID + '\n') if arguments[1] == 'ls' else json.dumps(payload)
        return SimpleNamespace(streams=SimpleNamespace(stdout=SimpleNamespace(text=text)))
    monkeypatch.setattr(client, '_run_docker_client', runner)
    return calls


def run(recovery):
    return asyncio.run(service.reconcile_sample_command(recovery=recovery))


@pytest.mark.parametrize('status', ['created', 'running', 'exited'])
@pytest.mark.parametrize('phase', ['creating', 'executing', 'adapting', 'cleaning_container', 'cleaning_sample'])
def test_fresh_container_state_ignores_old_phase_and_flags(monkeypatch, sample, status, phase):
    calls = install(monkeypatch, status=status)
    recovery = recovery_for(sample, phase=phase, stop_confirmed=True, container_absent=True)
    before = dict(samples._ACTIVE_SAMPLES)
    result = run(recovery)
    assert result.container_status == status and result.container_id == CID
    assert result.id_source == 'record' and result.identity.container_id == CID
    assert result.sample_status == 'identity_matches_record'
    assert result.recovery is recovery and recovery.command is None
    assert recovery.container_absent is True
    assert calls[-1] == ('container', 'inspect', CID) and len(calls) == 2
    assert samples._ACTIVE_SAMPLES == before
    with pytest.raises(FrozenInstanceError):
        result.container_status = 'absent'


def test_confirmed_absence_does_not_discover_same_name(monkeypatch, sample):
    calls = install(monkeypatch, absent=True)
    result = run(recovery_for(sample))
    assert result.container_status == 'absent' and result.identity is None
    assert result.container_id == CID and result.id_source == 'record'
    assert result.sample_status == 'identity_matches_record'
    assert len(calls) == 1 and calls[0][1] == 'ls'


@pytest.mark.parametrize('failure', ['ls', 'inspect'])
def test_docker_error_keeps_source_result_and_never_fakes_absence(monkeypatch, sample, failure):
    calls = install(monkeypatch, failure=failure)
    result = run(recovery_for(sample))
    assert result.container_status == 'unconfirmed'
    assert result.sample_status == 'identity_matches_record'
    assert result.container_id == CID and result.id_source == 'record'
    assert all(args[-1] != f'agent-sandbox-{TOKEN}' for args in calls)
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('status', ['created', 'running', 'exited'])
def test_unknown_id_discovery_only_from_created(monkeypatch, sample, status):
    calls = install(monkeypatch, status=status)
    recovery = recovery_for(sample, phase='creating', container_id=None)
    result = run(recovery)
    assert recovery.container_id is None
    assert calls[0] == ('container', 'inspect', f'agent-sandbox-{TOKEN}')
    if status == 'created':
        assert result.container_status == 'created' and result.container_id == CID
        assert result.id_source == 'discovered' and len(calls) == 2
    else:
        assert result.container_status == 'unconfirmed' and result.container_id is None
        assert result.id_source == 'unknown' and len(calls) == 1


def test_unknown_id_failed_name_lookup_is_not_absent(monkeypatch, sample):
    calls = install(monkeypatch, failure='inspect')
    result = run(recovery_for(sample, phase='creating', container_id=None))
    assert result.container_status == 'unconfirmed' and result.id_source == 'unknown'
    assert len(calls) == 1


@pytest.mark.parametrize('field', ['id', 'name', 'token', 'pid', 'paused'])
def test_bad_identity_or_state_never_falls_back(monkeypatch, sample, field):
    def mutate(payload):
        item = payload[0]
        if field == 'id':
            item['Id'] = 'b' * 64
        elif field == 'name':
            item['Name'] = '/wrong'
        elif field == 'token':
            item['Config']['Labels']['ai-agent-learning-lab.execution'] = 'f' * 32
        elif field == 'pid':
            item['State']['Pid'] = True
        else:
            item['State']['Paused'] = True
    calls = install(monkeypatch, mutate=mutate)
    result = run(recovery_for(sample))
    assert result.container_status == 'unconfirmed'
    assert result.sample_status == 'identity_matches_record'
    assert len(calls) == 2 and calls[-1][-1] == CID


def test_not_attempted_partial_site_never_probed(monkeypatch):
    calls = install(monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('raw path must not be opened')
    monkeypatch.setattr(samples.os, 'open', forbidden)
    recovery = SampleCommandRecovery(
        execution_token=TOKEN, container_name=f'agent-sandbox-{TOKEN}',
        argv=tuple(request().argv), working_directory='.',
        sample_root=Path('/etc'), sample_token='a' * 32,
    )
    result = run(recovery)
    assert result.container_status == 'not_attempted'
    assert result.sample_status == 'unregistered' and calls == []


@pytest.mark.parametrize('changes', [
    {'container_id': 'short'}, {'container_id': 'A' * 64}, {'container_name': 'wrong'},
    {'execution_token': 'bad'}, {'phase': 'wrong'}, {'create_attempted': 1},
    {'delete_attempted': 0}, {'stop_confirmed': 1}, {'container_id': None},
    {'create_attempted': False}, {'sample_token': 'wrong'}, {'sample_root': Path('/etc')},
    {'argv': ['/bin/true']}, {'working_directory': 'subdir'},
])
def test_invalid_recovery_rejected_before_io(monkeypatch, sample, changes):
    calls = install(monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('validation must precede source observation')
    monkeypatch.setattr(service, 'observe_sandbox_sample', forbidden)
    with pytest.raises((ValueError, TypeError)):
        run(recovery_for(sample, **changes))
    assert calls == []


def test_cancel_preserves_recovery_and_settles_query(monkeypatch, sample):
    async def scenario():
        entered, done = asyncio.Event(), asyncio.Event()
        async def runner(arguments):
            assert arguments[:2] == ('container', 'ls')
            entered.set()
            try:
                await asyncio.Future()
            finally:
                done.set()
        monkeypatch.setattr(client, '_run_docker_client', runner)
        recovery = recovery_for(sample)
        task = asyncio.create_task(service.reconcile_sample_command(recovery=recovery))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(service.SampleReconciliationCancelled) as caught:
                await task
            assert caught.value.recovery is recovery and done.is_set() and task.cancelled()
            assert sample.root.exists()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(asyncio.wait_for(scenario(), 2))


def test_sample_observation_is_readonly_and_closes_descriptors(monkeypatch, sample):
    path = sample.root / samples.SAMPLE_FILENAME
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns,
              sample.root.stat().st_mtime_ns, sample.root.parent.stat().st_mtime_ns)
    registry = dict(samples._ACTIVE_SAMPLES)
    real_open = os.open
    descriptors = []
    def readonly_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        fd = real_open(path, flags, *args, **kwargs)
        descriptors.append(fd)
        return fd
    with monkeypatch.context() as patch:
        patch.setattr(samples.os, 'open', readonly_open)
        assert samples.observe_sandbox_sample(sample) == 'identity_matches_record'
    assert samples._ACTIVE_SAMPLES == registry
    assert before == (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns,
                      sample.root.stat().st_mtime_ns, sample.root.parent.stat().st_mtime_ns)
    for fd in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize('level', ['root', 'parent'])
@pytest.mark.parametrize('replacement', ['missing', 'directory', 'symlink'])
def test_sample_path_missing_or_replaced(monkeypatch, sample, level, replacement):
    calls = install(monkeypatch, status='exited')
    target = sample.root if level == 'root' else sample.root.parent
    held = target.with_name(target.name + '-held')
    target.rename(held)
    try:
        if replacement == 'directory':
            target.mkdir()
        elif replacement == 'symlink':
            target.symlink_to(held, target_is_directory=True)
        result = run(recovery_for(sample))
        assert result.sample_status == ('missing' if replacement == 'missing' else 'identity_changed')
        assert result.container_status == 'exited' and len(calls) == 2
        assert held.exists()
    finally:
        if target.is_symlink():
            target.unlink()
        elif replacement == 'directory':
            target.rmdir()
        held.rename(target)


@pytest.mark.parametrize('change', ['file_missing', 'file_link', 'extra', 'permissions'])
def test_sample_structure_changes_are_not_missing(sample, change):
    path = sample.root / samples.SAMPLE_FILENAME
    held = sample.root.parent.parent / (sample.token + '-file-held')
    try:
        if change in ('file_missing', 'file_link'):
            path.rename(held)
            if change == 'file_link':
                path.symlink_to(held)
        elif change == 'extra':
            (sample.root / 'extra').write_bytes(b'extra')
        else:
            path.chmod(0o600)
        assert samples.observe_sandbox_sample(sample) == 'identity_changed'
    finally:
        if change in ('file_missing', 'file_link'):
            if path.is_symlink():
                path.unlink()
            held.rename(path)
        elif change == 'extra':
            (sample.root / 'extra').unlink()
        else:
            path.chmod(0o666)


@pytest.mark.parametrize('error', [PermissionError(errno.EACCES, 'PRIVATE'), OSError(errno.EIO, 'PRIVATE'), RuntimeError('PRIVATE')])
def test_source_error_keeps_unknown_without_losing_container(monkeypatch, sample, error):
    install(monkeypatch, status='running')
    real_open = samples.os.open
    def fail_open(path, *args, **kwargs):
        if path == sample.root.name:
            raise error
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(samples.os, 'open', fail_open)
        result = run(recovery_for(sample))
        assert result.sample_status == 'unconfirmed' and result.container_status == 'running'
    assert 'PRIVATE' not in repr(result)


def test_unregistered_equal_object_and_expired_sample_never_read(monkeypatch, sample):
    clone = replace(sample)
    with monkeypatch.context() as patch:
        patch.setattr(samples.os, 'open', lambda *a, **k: pytest.fail('unregistered read'))
        assert samples.observe_sandbox_sample(clone) == 'unregistered'
        assert samples.observe_sandbox_sample(None) == 'unregistered'
    samples.cleanup_sandbox_sample(sample)
    with monkeypatch.context() as patch:
        patch.setattr(samples.os, 'open', lambda *a, **k: pytest.fail('expired read'))
        assert samples.observe_sandbox_sample(sample) == 'unregistered'


def test_identity_match_does_not_claim_content_snapshot(monkeypatch, sample):
    path = sample.root / samples.SAMPLE_FILENAME
    original = path.read_bytes()
    try:
        path.write_bytes(b'changed by owner\n')
        with monkeypatch.context() as patch:
            patch.setattr(samples.os, 'read', lambda *args: pytest.fail('diagnostic must not read contents'))
            assert samples.observe_sandbox_sample(sample) == 'identity_matches_record'
    finally:
        path.write_bytes(original)


def test_current_query_never_rewrites_previous_command_result(monkeypatch, sample):
    from app.services.runtime.command.command_contracts import CommandResult

    install(monkeypatch, status='running')
    command = CommandResult(status='exited', exit_code=7, oom_killed=False,
                            daemon_error=False, stdout='previous output', duration_ms=1)
    recovery = recovery_for(sample, command=command, stop_confirmed=True)
    result = run(recovery)
    assert result.container_status == 'running'
    assert result.recovery.command is command
    assert command.exit_code == 7 and command.stdout == 'previous output'


def test_both_failed_reads_remain_independently_unknown(monkeypatch, sample):
    install(monkeypatch, failure='ls')
    real_open = os.open
    def denied(path, *args, **kwargs):
        if path == sample.root.name:
            raise PermissionError(errno.EACCES, 'PRIVATE')
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(samples.os, 'open', denied)
        result = run(recovery_for(sample))
    assert result.container_status == result.sample_status == 'unconfirmed'
    assert result.identity is None and result.recovery.command is None
