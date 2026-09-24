"""请求级记录与真实样例生命周期组合；不调用外部 Docker/数据库。"""

import asyncio
import json
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from app.services.runtime.sandbox import recorded_task_sample_command as service
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.sandbox import task_sample_command_journal as records
from tests.runtime.sandbox.test_sandbox_creation import request
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample_command import lab as lab  # noqa: PLC0414
from tests.runtime.sandbox.test_task_sample_command import factory as factory  # noqa: PLC0414


@pytest.fixture
def journal():
    return records.TaskSampleCommandJournal(user_id=1, conversation_id='conversation')


def run(journal, command=None, **changes):
    return service.run_recorded_task_sample_command(**({
        'request': request() if command is None else command,
        'user_id': 1, 'conversation_id': 'conversation', 'bindings': None, 'journal': journal,
    } | changes))


@pytest.mark.parametrize('code', [0, 7])
def test_completed_result_is_immutable_and_nonzero_is_not_unknown(lab, factory, journal, code):
    lab.code = code
    lab.mutate = lambda: journal.close()
    result = asyncio.run(run(journal))
    record, = journal.records
    assert record.status == 'completed' and record.sample_cleaned and record.recovery is None
    assert record.sample_token == result.sample_token and record.container_id == result.cleanup.container_id
    assert json.loads(record.command_json)['exit_code'] == code
    with pytest.raises(ValidationError):
        result.command.stdout = 'changed outside'
    assert json.loads(journal.records[0].command_json)['stdout'] != 'changed outside'
    with pytest.raises(FrozenInstanceError):
        record.status = 'pending'
    with pytest.raises(records.TaskSampleJournalUnavailable):
        journal.reserve(request())


@pytest.mark.parametrize('stage', ['create', 'inspect_create', 'execute', 'inspect_cleanup', 'remove', 'absent', 'cleanup_sample'])
@pytest.mark.parametrize('cancel', [False, True])
def test_full_failure_evidence_saved_before_propagation(lab, factory, journal, stage, cancel):
    lab.failure = stage
    lab.error = asyncio.CancelledError() if cancel else OSError('PRIVATE')
    error_type = owner.SampleCommandCancelled if cancel else owner.SampleCommandUnconfirmed
    with pytest.raises(error_type) as caught:
        asyncio.run(run(journal))
    record, = journal.records
    assert record.status == ('cancelled' if cancel else 'unconfirmed')
    assert record.recovery.sample is caught.value.recovery.sample is lab.samples[0]
    assert record.recovery.sample_root.exists()
    assert record.recovery.phase == caught.value.recovery.phase
    assert record.recovery.container_id == caught.value.recovery.container_id
    if record.recovery.command is not None:
        with pytest.raises(ValidationError):
            caught.value.recovery.command.stdout = 'exception changed'
        with pytest.raises(ValidationError):
            record.recovery.command.stdout = 'returned record changed'
        assert journal.records[0].recovery.command.stdout == '你好\n'
    assert record.command_json is None
    # 记录关闭仅禁止新登记，既不清理快照，也不丢弃失败身份。
    journal.close()
    assert journal.records[0].recovery.sample.root.exists()


@pytest.mark.parametrize('kind', ['closed', 'full', 'wrong_user', 'wrong_conversation', 'missing_journal', 'bad_request'])
def test_gate_rejects_before_preparation(lab, factory, journal, kind):
    args = {}
    if kind == 'closed':
        journal.close()
    elif kind == 'full':
        for _ in range(records.MAX_TASK_SAMPLE_COMMAND_RECORDS):
            journal.reserve(request())
    elif kind == 'wrong_user':
        args['user_id'] = 2
    elif kind == 'wrong_conversation':
        args['conversation_id'] = 'different'
    elif kind == 'missing_journal':
        journal = None
    else:
        command = request()
        command.argv[0] = 'relative'
        args['command'] = command
    before = None if journal is None else journal.records
    with pytest.raises(ValueError):
        asyncio.run(run(journal, **args))
    assert lab.samples == [] and lab.calls == []
    if journal is not None:
        assert journal.records == before


def test_request_is_reserved_before_work_and_copied(lab, factory, journal):
    command = request()
    def mutate():
        assert journal.records[0].status == 'pending'
        command.argv[:] = ['/changed']
    lab.mutate = mutate
    asyncio.run(run(journal, command))
    assert journal.records[0].argv == tuple(request().argv)
    copy = journal.records[0].build_request()
    copy.argv[:] = ['/another']
    assert journal.records[0].argv == tuple(request().argv)


@pytest.mark.parametrize('cancel', [False, True])
def test_untyped_failure_stays_unknown_without_fabricated_identity(journal, monkeypatch, cancel):
    async def fail(**kwargs):
        raise asyncio.CancelledError() if cancel else OSError('PRIVATE')
    monkeypatch.setattr(service, 'run_task_sample_command', fail)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError) as caught:
        asyncio.run(run(journal))
    record, = journal.records
    assert record.status == ('cancelled' if cancel else 'unconfirmed')
    assert record.recovery is None and record.container_id is None
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('cancel', [False, True])
def test_preparing_partial_evidence_not_discarded(journal, lab, monkeypatch, sample_base, cancel):
    recovery = owner.SampleCommandRecovery(
        execution_token='a' * 32, container_name='agent-sandbox-' + 'a' * 32,
        argv=tuple(request().argv), working_directory='.', sample_token='partial',
        sample_root=sample_base / 'partial',
    )
    error_type = owner.SampleCommandCancelled if cancel else owner.SampleCommandUnconfirmed
    async def fail(**kwargs):
        raise error_type(recovery=recovery)
    monkeypatch.setattr(service, 'run_task_sample_command', fail)
    with pytest.raises(error_type):
        asyncio.run(run(journal))
    saved = journal.records[0].recovery
    assert saved.sample_root == recovery.sample_root and saved.sample_token == 'partial'
    assert saved.phase == 'preparing' and not saved.create_attempted


@pytest.mark.parametrize('index', [-1, 1, True, '0'])
def test_bad_indices_do_not_change_pending_record(journal, index):
    journal.reserve(request())
    with pytest.raises(ValueError):
        journal.finish(index, status='unconfirmed')
    assert journal.records[0].status == 'pending'


@pytest.mark.parametrize('change', ['status', 'completed_without_result', 'recovery_type', 'mismatch'])
def test_invalid_terminal_rejected_atomically(journal, change):
    journal.reserve(request())
    kwargs = {'status': 'unconfirmed'}
    if change == 'status':
        kwargs['status'] = 'invalid'
    elif change == 'completed_without_result':
        kwargs['status'] = 'completed'
    elif change == 'recovery_type':
        kwargs['recovery'] = object()
    else:
        kwargs['recovery'] = owner.SampleCommandRecovery(
            execution_token='a' * 32, container_name='agent-sandbox-' + 'a' * 32,
            argv=('/bin/false',), working_directory='.',
        )
    with pytest.raises((TypeError, ValueError)):
        journal.finish(0, **kwargs)
    assert journal.records[0].status == 'pending'


def test_closed_pending_can_finish_only_once(journal):
    index = journal.reserve(request())
    journal.close()
    journal.finish(index, status='cancelled')
    with pytest.raises(ValueError):
        journal.finish(index, status='unconfirmed')
    assert journal.records[0].status == 'cancelled'


def test_completed_record_is_not_evicted_for_capacity(journal, monkeypatch):
    monkeypatch.setattr(records, 'MAX_TASK_SAMPLE_COMMAND_RECORDS', 1)
    index = journal.reserve(request())
    journal.finish(index, status='unconfirmed')
    with pytest.raises(records.TaskSampleJournalUnavailable):
        journal.reserve(request())
    assert len(journal.records) == 1


@pytest.mark.parametrize('user_id,conversation_id', [(True, 'c'), (0, 'c'), (1, ''), (1, None)])
def test_bad_scope_rejected(user_id, conversation_id):
    with pytest.raises(records.TaskSampleJournalUnavailable):
        records.TaskSampleCommandJournal(user_id=user_id, conversation_id=conversation_id)


def test_concurrent_capacity_and_close_do_not_interrupt_reserved_attempt(lab, factory, journal, monkeypatch):
    monkeypatch.setattr(records, 'MAX_TASK_SAMPLE_COMMAND_RECORDS', 1)
    original = service.run_task_sample_command
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def delayed(**kwargs):
            entered.set()
            await release.wait()
            return await original(**kwargs)
        monkeypatch.setattr(service, 'run_task_sample_command', delayed)
        first = asyncio.create_task(run(journal))
        await entered.wait()
        try:
            with pytest.raises(records.TaskSampleJournalUnavailable):
                await run(journal)
            assert lab.samples == []
            journal.close()
        finally:
            release.set()
        assert (await first).sample_cleaned
        assert len(journal.records) == 1 and journal.records[0].status == 'completed'
    asyncio.run(scenario())


def test_actual_preparation_cancel_stores_completed_snapshot(lab, journal, monkeypatch):
    from threading import Event
    from app.services.runtime.sandbox import task_sample_command as task_service
    from app.services.runtime.sandbox import sandbox_sample as samples

    entered = Event()
    release = Event()
    def prepare(**kwargs):
        entered.set()
        assert release.wait(5)
        sample = samples.create_sandbox_snapshot(content=b'cancelled preparation')
        lab.samples.append(sample)
        return sample
    monkeypatch.setattr(task_service, 'create_task_sandbox_snapshot', prepare)
    async def scenario():
        task = asyncio.create_task(run(journal))
        async def wait_entered():
            while not entered.is_set():
                await asyncio.sleep(0.001)
        try:
            await asyncio.wait_for(wait_entered(), 2)
            task.cancel()
            await asyncio.sleep(0)
            journal.close()
            assert journal.records[0].status == 'pending'
        finally:
            release.set()
        with pytest.raises(owner.SampleCommandCancelled):
            await task
        record, = journal.records
        assert record.status == 'cancelled' and record.recovery.sample is lab.samples[0]
        assert not record.recovery.create_attempted and record.recovery.sample.root.exists()
        assert lab.calls == []
    asyncio.run(scenario())
