"""记录先于副作用，终态先于公开结果；不运行数据库或Docker。"""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.runtime.sandbox.sandbox_sample_command import (
    SampleCommandCancelled, SampleCommandRecovery, SampleCommandUnconfirmed,
)
from app.services.runtime.sandbox.task_sample_command_journal import (
    MAX_TASK_SAMPLE_COMMAND_RECORDS, TaskSampleJournalUnavailable,
)
from app.services.runtime.verification import recorded_task_verification as service
from app.services.runtime.verification.contracts import VerificationRequest, build_verification_command
from app.services.runtime.verification.sandbox_verification import VerificationAdaptationError, adapt_sample_verification
from app.services.runtime.verification.verification_journal import TaskVerificationJournal
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools import recorded_task_verification as tool
from app.tools import task_verification as projection
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY
from tests.assertions import require_value
from tests.runtime.verification.test_sandbox_verification import receipt

CONTEXT = ToolExecutionContext(1, 'c', 'w', 't')
REQUEST = VerificationRequest(plan_id='sample_unittest_v1')


def recovery(phase='preparing', **changes):
    command = build_verification_command(REQUEST.plan_id)
    return SampleCommandRecovery(**({
        'execution_token': 'a' * 32, 'container_name': 'agent-sandbox-' + 'a' * 32,
        'argv': tuple(command.argv), 'working_directory': '.', 'phase': phase,
        'container_id': None if phase == 'preparing' else 'b' * 64,
        'create_attempted': phase != 'preparing',
    } | changes))


def definition(journal):
    return tool.make_recorded_task_verification_definition(bindings=TaskSampleBindings(), journal=journal)


def run(defn, context=CONTEXT):
    return asyncio.run(defn.execute_async(REQUEST, context=context))


@pytest.mark.parametrize('exit_code', [0, 1])
def test_reserve_before_execute_record_before_projection(monkeypatch, exit_code):
    journal = TaskVerificationJournal(context=CONTEXT)
    value = adapt_sample_verification(receipt(exit_code=exit_code))
    calls = []
    async def execute(**kwargs):
        calls.append('execute')
        assert journal.records[0].status == 'pending'
        assert kwargs['context'] == CONTEXT and kwargs['request'] == REQUEST
        return value
    project = projection.project_task_verification_result
    def observe(result):
        calls.append('project')
        record = journal.records[0]
        assert record.status == 'completed' and record.sample_cleaned
        assert json.loads(require_value(record.command_json))['exit_code'] == exit_code
        return project(result)
    monkeypatch.setattr(service, 'run_task_verification', execute)
    monkeypatch.setattr(projection, 'project_task_verification_result', observe)
    defn = definition(journal)
    assert defn.name not in TOOL_REGISTRY
    public = json.loads(run(defn))
    assert public['outcome'] == ('passed' if exit_code == 0 else 'failed')
    assert calls == ['execute', 'project']


@pytest.mark.parametrize('field,value', [('user_id', 2), ('user_id', True), ('conversation_id', 'other'), ('workspace_id', 'other'), ('task_id', 'other')])
def test_scope_mismatch_before_reservation(monkeypatch, field, value):
    journal = TaskVerificationJournal(context=CONTEXT)
    async def forbidden(**kwargs):
        pytest.fail('wrong scope executed')
    monkeypatch.setattr(service, 'run_task_verification', forbidden)
    with pytest.raises(SafeToolExecutionError) as caught:
        run(definition(journal), replace(CONTEXT, **{field: value}))
    assert caught.value.code == 'verification_recovery_unavailable' and not journal.records


@pytest.mark.parametrize('state', ['closed', 'full'])
def test_unavailable_journal_never_executes(monkeypatch, state):
    journal = TaskVerificationJournal(context=CONTEXT)
    if state == 'closed':
        journal.close()
    else:
        for _ in range(MAX_TASK_SAMPLE_COMMAND_RECORDS):
            journal.reserve(build_verification_command(REQUEST.plan_id))
    before = journal.records
    async def forbidden(**kwargs):
        pytest.fail('journal unavailable')
    monkeypatch.setattr(service, 'run_task_verification', forbidden)
    with pytest.raises(SafeToolExecutionError) as caught:
        run(definition(journal))
    assert caught.value.code == 'verification_recovery_unavailable' and journal.records == before


@pytest.mark.parametrize('phase', ['preparing', 'creating', 'executing', 'cleaning_container', 'cleaning_sample'])
@pytest.mark.parametrize('cancelled', [False, True])
def test_recovery_saved_before_safe_error_or_cancel(monkeypatch, phase, cancelled):
    journal = TaskVerificationJournal(context=CONTEXT)
    evidence = recovery(phase, command=receipt().command if phase.startswith('cleaning') else None)
    failure = (SampleCommandCancelled if cancelled else SampleCommandUnconfirmed)(recovery=evidence)
    async def execute(**kwargs):
        raise failure
    monkeypatch.setattr(service, 'run_task_verification', execute)
    with pytest.raises(SampleCommandCancelled if cancelled else SafeToolExecutionError) as caught:
        run(definition(journal))
    record = journal.records[0]
    assert record.status == ('cancelled' if cancelled else 'unconfirmed')
    assert record.recovery is evidence
    if cancelled:
        assert caught.value is failure
    else:
        assert isinstance(caught.value, SafeToolExecutionError)
        assert evidence.container_name not in str(caught.value)
        assert caught.value.code == ('verification_cleanup_unconfirmed' if phase.startswith('cleaning') else 'verification_execution_unconfirmed')


@pytest.mark.parametrize('kind', ['partial', 'timeout', 'unknown'])
def test_partial_and_timeout_or_unknown_evidence(monkeypatch, kind):
    journal = TaskVerificationJournal(context=CONTEXT)
    evidence = recovery(sample_token='private', sample_root=Path('/PRIVATE/partial')) if kind == 'partial' else recovery('executing', execution_reason='timed_out')
    async def execute(**kwargs):
        if kind == 'unknown':
            raise RuntimeError('/PRIVATE/internal error')
        raise SampleCommandUnconfirmed(recovery=evidence)
    monkeypatch.setattr(service, 'run_task_verification', execute)
    with pytest.raises(SafeToolExecutionError) as caught:
        run(definition(journal))
    record = journal.records[0]
    assert record.status == 'unconfirmed'
    assert record.recovery is (None if kind == 'unknown' else evidence)
    assert '/PRIVATE' not in str(caught.value)
    if kind == 'timeout':
        assert caught.value.code == 'verification_timeout_unconfirmed'


@pytest.mark.parametrize('stage', ['adapt', 'project'])
def test_completed_receipt_survives_result_failure(monkeypatch, stage):
    journal = TaskVerificationJournal(context=CONTEXT)
    execution = receipt()
    async def execute(**kwargs):
        if stage == 'adapt':
            raise VerificationAdaptationError(execution)
        return adapt_sample_verification(execution)
    monkeypatch.setattr(service, 'run_task_verification', execute)
    if stage == 'project':
        monkeypatch.setattr(projection, 'MAX_PUBLIC_RESULT_BYTES', 1)
    with pytest.raises(SafeToolExecutionError):
        run(definition(journal))
    record = journal.records[0]
    assert record.status == 'completed' and record.sample_cleaned
    assert record.container_id == execution.cleanup.container_id
    assert record.command_json == execution.command.model_dump_json()
    assert record.sample_token == execution.sample_token


def test_actual_task_cancel_after_close_finishes_once(monkeypatch):
    journal = TaskVerificationJournal(context=CONTEXT)
    async def scenario():
        entered = asyncio.Event()
        async def execute(**kwargs):
            entered.set()
            await asyncio.Future()
        monkeypatch.setattr(service, 'run_task_verification', execute)
        task = asyncio.create_task(definition(journal).execute_async(REQUEST, context=CONTEXT))
        await entered.wait()
        assert journal.records[0].status == 'pending'
        journal.close()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert journal.records[0].status == 'cancelled'
        with pytest.raises(ValueError):
            journal.finish(0, status='unconfirmed')
        with pytest.raises(TaskSampleJournalUnavailable):
            journal.reserve(build_verification_command(REQUEST.plan_id))
    asyncio.run(scenario())


@pytest.mark.parametrize('changes', [{'user_id': True}, {'user_id': 0}, {'conversation_id': ''}, {'workspace_id': ''}, {'task_id': ''}])
def test_invalid_journal_scope(changes):
    with pytest.raises(TaskSampleJournalUnavailable):
        TaskVerificationJournal(context=replace(CONTEXT, **changes))


def test_invalid_plan_does_not_reserve_or_execute(monkeypatch):
    journal = TaskVerificationJournal(context=CONTEXT)
    async def forbidden(**kwargs):
        pytest.fail('invalid plan executed')
    monkeypatch.setattr(service, 'run_task_verification', forbidden)
    with pytest.raises(ValueError):
        asyncio.run(service.run_recorded_task_verification(
            request=VerificationRequest.model_construct(plan_id='other'), context=CONTEXT,
            bindings=TaskSampleBindings(), journal=journal,
        ))
    assert not journal.records
