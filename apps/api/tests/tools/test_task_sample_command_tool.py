"""内部工具适配→作用域→记录→样例编排组合；不注册模型能力。"""

import asyncio
import json
from dataclasses import replace
from threading import Event

import pytest
from pydantic import ValidationError

from app.services.runtime.command.command_contracts import CommandRequest, CommandResult
from app.services.runtime.execution.task_sample_recovery_store import TaskSampleRecoveryStore
from app.services.runtime.execution import task_sample_recovery_store as stores
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.sandbox import task_sample_command as commands
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools import task_sample_command as tool
from app.tools.errors import SafeToolExecutionError
from tests.runtime.sandbox.test_sandbox_creation import CID, request
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample_command import lab as lab  # noqa: PLC0414


@pytest.fixture
def context():
    return tool.TaskSampleCommandToolContext(
        user_id=1, conversation_id='conversation', run_id=10,
        store=TaskSampleRecoveryStore(), bindings=TaskSampleBindings(),
    )


@pytest.fixture
def factory(lab, context, monkeypatch):
    def create(**kwargs):
        assert kwargs == {'user_id': 1, 'conversation_id': 'conversation', 'bindings': context.bindings}
        sample = samples.create_sandbox_snapshot(content=b'private source bytes')
        lab.samples.append(sample)
        return sample
    monkeypatch.setattr(commands, 'create_task_sandbox_snapshot', create)
    return create


def invoke(context, **changes):
    return tool.run_task_sample_command_tool(**({'argv': request().argv, 'context': context} | changes))


def saved(context):
    return context.store.get(user_id=1, conversation_id='conversation', run_id=10).journal.records[0]


@pytest.mark.parametrize('code', [0, 7, 137])
def test_success_only_projects_command_result(lab, factory, context, code):
    lab.code = code
    text = asyncio.run(invoke(context))
    record = saved(context)
    assert record.status == 'completed' and text == record.command_json
    payload = json.loads(text)
    assert payload['exit_code'] == code
    assert CommandResult.model_validate(payload).succeeded is (code == 0)
    assert payload['stdout'] == '你好\n'
    for private in ('sample_token', 'container_id', 'execution_token', 'sample_root', str(lab.samples[0].root), lab.samples[0].token):
        assert private not in text


@pytest.mark.parametrize('stage,expected', [
    ('create', 'command_creation_unconfirmed'),
    ('inspect_create', 'command_creation_unconfirmed'),
    ('execute', 'command_execution_unconfirmed'),
    ('inspect_cleanup', 'command_cleanup_unconfirmed'),
    ('remove', 'command_cleanup_unconfirmed'),
    ('absent', 'command_cleanup_unconfirmed'),
    ('cleanup_sample', 'command_cleanup_unconfirmed'),
])
def test_failure_safe_projection_preserves_internal_record(lab, factory, context, stage, expected):
    lab.failure = stage
    lab.error = OSError('PRIVATE docker credentials /host/path')
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context))
    error = caught.value
    assert error.code == expected and error.__suppress_context__
    assert not hasattr(error, 'recovery')
    public = json.dumps({'code': error.code, 'message': error.message})
    record = saved(context)
    assert record.status == 'unconfirmed' and record.recovery.sample is lab.samples[0]
    for secret in ('PRIVATE', str(lab.samples[0].root), lab.samples[0].token):
        assert secret not in public


def test_timeout_maps_with_stop_evidence(lab, factory, context):
    lab.failure = 'execute'
    lab.error = owner.SandboxExecutionUnconfirmed(
        execution_token='b' * 32, container_id=CID, reason='timed_out',
        start_attempted=True, stop_confirmed=True,
    )
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context))
    assert caught.value.code == 'command_timeout_unconfirmed'
    assert saved(context).recovery.stop_confirmed


def test_preparation_partial_failure_is_not_reported_as_safe_retry(lab, context, monkeypatch, sample_base):
    def fail(**kwargs):
        raise samples.SandboxSampleCreationUnconfirmed(token='PRIVATE', root=sample_base / 'partial')
    monkeypatch.setattr(commands, 'create_task_sandbox_snapshot', fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context))
    assert caught.value.code == 'task_command_preparation_unconfirmed'
    assert saved(context).recovery.sample_root == sample_base / 'partial'
    assert not saved(context).recovery.create_attempted and lab.calls == []
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('changes', [{'argv': []}, {'argv': 'text'}, {'argv': ['/x\x00']}, {'argv': ['relative']}, {'working_directory': 'src'}, {'working_directory': '../x'}])
def test_bad_parameters_do_not_allocate_scope(lab, factory, context, changes):
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context, **changes))
    assert caught.value.code == 'command_request_rejected'
    assert context.store.get(user_id=1, conversation_id='conversation', run_id=10) is None
    assert lab.calls == [] and lab.samples == []


@pytest.mark.parametrize('kind', ['context', 'store', 'bindings', 'identity', 'closed_store', 'full_store', 'wrong_owner', 'closed_journal', 'full_journal'])
def test_context_and_capacity_fail_before_preparation(lab, factory, context, monkeypatch, kind):
    if kind == 'context':
        context = None
    elif kind in ('store', 'bindings'):
        context = replace(context, **{kind: None})
    elif kind == 'identity':
        context = replace(context, run_id=True)
    elif kind == 'closed_store':
        context.store.close()
    elif kind == 'full_store':
        monkeypatch.setattr(stores, 'MAX_TASK_SAMPLE_RECOVERY_SCOPES', 0)
    else:
        scope = context.store.acquire(user_id=1, conversation_id='conversation', run_id=10)
        if kind == 'wrong_owner':
            context = replace(context, user_id=2)
        elif kind == 'closed_journal':
            scope.journal.close()
        else:
            for _ in range(16):
                scope.journal.reserve(request())
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context))
    assert caught.value.code == 'command_recovery_unavailable'
    assert lab.samples == [] and lab.calls == []


@pytest.mark.parametrize('stage', ['create', 'execute', 'remove'])
def test_cancel_propagates_after_recording(lab, factory, context, stage):
    lab.failure = stage
    lab.error = asyncio.CancelledError('PRIVATE cancellation')
    with pytest.raises(owner.SampleCommandCancelled) as caught:
        asyncio.run(invoke(context))
    record = saved(context)
    assert record.status == 'cancelled' and record.recovery is caught.value.recovery
    assert record.recovery.sample is lab.samples[0]


def test_preparation_cancel_waits_records_and_propagates(lab, context, monkeypatch):
    entered, release = Event(), Event()
    def prepare(**kwargs):
        entered.set()
        assert release.wait(5)
        sample = samples.create_sandbox_snapshot(content=b'cancelled')
        lab.samples.append(sample)
        return sample
    monkeypatch.setattr(commands, 'create_task_sandbox_snapshot', prepare)
    async def scenario():
        task = asyncio.create_task(invoke(context))
        async def wait():
            while not entered.is_set():
                await asyncio.sleep(0.001)
        try:
            await asyncio.wait_for(wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            assert saved(context).status == 'pending'
        finally:
            release.set()
        with pytest.raises(owner.SampleCommandCancelled):
            await task
        assert saved(context).status == 'cancelled' and saved(context).recovery.sample is lab.samples[0]
        assert lab.calls == []
    asyncio.run(scenario())


def test_unexpected_exception_is_not_reflected(context, monkeypatch):
    async def fail(**kwargs):
        raise RuntimeError('PRIVATE unknown failure')
    monkeypatch.setattr(tool, 'run_scoped_task_sample_command', fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context))
    assert caught.value.code == 'command_result_unavailable' and 'PRIVATE' not in str(caught.value)


def test_schema_reuses_contract_without_server_fields():
    assert tool.RunTaskSampleCommandArguments is CommandRequest
    assert set(CommandRequest.model_json_schema()['properties']) == {'argv', 'working_directory'}
    for key in ('user_id', 'conversation_id', 'run_id', 'store', 'bindings', 'root_path'):
        with pytest.raises(ValidationError):
            CommandRequest(argv=['/bin/true'], **{key: 'PRIVATE'})


def test_result_adaptation_failure_preserves_snapshot(lab, factory, context, monkeypatch):
    def fail(value):
        raise RuntimeError('PRIVATE output conversion')
    monkeypatch.setattr(owner, 'build_command_result', fail)
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context))
    assert caught.value.code == 'command_result_unavailable'
    assert saved(context).recovery.phase == 'adapting'
    assert saved(context).recovery.sample.root.exists()


def test_public_projection_failure_does_not_overwrite_completed_record(lab, factory, context, monkeypatch):
    from types import SimpleNamespace
    original = tool.run_scoped_task_sample_command
    class BadProjection:
        def model_dump_json(self):
            raise RuntimeError('PRIVATE serialization')
    async def project_failure(**kwargs):
        await original(**kwargs)
        return SimpleNamespace(command=BadProjection())
    monkeypatch.setattr(tool, 'run_scoped_task_sample_command', project_failure)
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(invoke(context))
    assert caught.value.code == 'command_result_unavailable'
    assert saved(context).status == 'completed' and saved(context).sample_cleaned
    assert saved(context).command_json is not None
