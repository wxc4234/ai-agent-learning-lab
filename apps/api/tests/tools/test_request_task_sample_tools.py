"""请求能力选择、受控模型调用与样例恢复作用域收尾。"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers.chat import chat_execution as owners
from app.schemas import ChatRequest
from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import run_agent_loop
from app.services.runtime.execution.command_recovery_store import CommandRecoveryStore
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.runtime.execution.task_sample_recovery_store import TaskSampleRecoveryStore
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import task_sample_command as commands
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings, TaskSampleStatus, TaskSampleBindingError
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, tools_for_execution, ToolContextRequiredError
from tests.runtime.sandbox.test_sandbox_creation import request
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample_command import lab as lab  # noqa: PLC0414
from tests.model.test_model_decision import build_text_response, build_tool_response

CONTEXT = ToolExecutionContext(1, 'conversation', 'workspace', 'task')


async def make_owner(monkeypatch, status='ready'):
    bindings = TaskSampleBindings()
    monkeypatch.setattr(bindings, 'read_status', lambda **kwargs: TaskSampleStatus(status, None))
    monkeypatch.setattr(owners, 'load_tool_execution_context', lambda **kwargs: CONTEXT)
    monkeypatch.setattr(owners, 'finish_agent_run', lambda *args, **kwargs: None)
    owner = owners.ChatExecution(
        user_id=1, body=ChatRequest(session_id='conversation', prompt='test'), threads=ExecutionThreads(),
        command_recovery_store=CommandRecoveryStore(), task_sample_recovery_store=TaskSampleRecoveryStore(),
        sample_bindings=bindings,
    )
    async def created():
        return 10
    owner.creation = asyncio.create_task(created())
    await owner.creation
    return owner


@pytest.mark.parametrize('status', ['ready', 'missing', 'busy', 'sealed'])
def test_capability_profile_does_not_mutate_global_registry(monkeypatch, status):
    async def scenario():
        owner = await make_owner(monkeypatch, status)
        try:
            binding = await owner.bind_command_tool(CONTEXT)
            definitions = tools_for_execution(
                context=CONTEXT, command_executor=None if binding is None else binding.executor,
                sample_snapshot=binding is not None and binding.sample_snapshot,
            )
            commands = [item for item in definitions if item.name == 'run_command']
            assert bool(commands) is (status in ('ready', 'missing'))
            if commands:
                schema = commands[0].as_model_tool()['function']
                assert ('/workspace/example.txt' in schema['description']) is (status == 'ready')
                assert set(schema['parameters']['properties']) == {'argv', 'working_directory'}
                arguments = commands[0].validate_arguments(json.dumps({'argv': request().argv}))
                with pytest.raises(ToolContextRequiredError):
                    await commands[0].execute_async(arguments, context=replace(CONTEXT))
            assert owner.sample_scope is None and owner.command_scope is None
            assert 'run_command' not in TOOL_REGISTRY
        finally:
            await owner.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', ['success', 'nonzero', 'cleanup', 'cancel', 'timeout', 'stale_ready'])
def test_controlled_model_executes_snapshot_and_close_retains_records(lab, monkeypatch, outcome):
    def prepare(**kwargs):
        if outcome == 'stale_ready':
            raise TaskSampleBindingError()
        sample = samples.create_sandbox_snapshot(content=b'current task')
        lab.samples.append(sample)
        return sample
    monkeypatch.setattr(commands, 'create_task_sandbox_snapshot', prepare)
    if outcome == 'nonzero':
        lab.code = 7
    if outcome in ('cleanup', 'cancel'):
        lab.failure = 'remove' if outcome == 'cleanup' else 'execute'
        lab.error = OSError('PRIVATE') if outcome == 'cleanup' else asyncio.CancelledError()
    async def scenario():
        owner = await make_owner(monkeypatch)
        binding = await owner.bind_command_tool(CONTEXT)
        definitions = tools_for_execution(context=CONTEXT, command_executor=binding.executor, sample_snapshot=True)
        if outcome == 'timeout':
            import app.services.runtime.sandbox.sandbox_sample_command as lifecycle
            async def blocked(**kwargs):
                await asyncio.Future()
            monkeypatch.setattr(lifecycle, 'execute_created_sandbox', blocked)
            definitions = (*definitions[:-1], replace(definitions[-1], timeout_seconds=0.05))
        create = AsyncMock(side_effect=[
            build_tool_response(('call', 'run_command', json.dumps({'argv': request().argv}))),
            build_text_response('done'),
        ])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        model = DeepSeekDecisionMaker(client=client, model='test', messages=[{'role': 'user', 'content': 'test'}],
                                      tool_context=CONTEXT, tool_definitions=definitions)
        try:
            if outcome == 'cancel':
                with pytest.raises(asyncio.CancelledError):
                    await run_agent_loop(model, tool_context=CONTEXT, tool_definitions=definitions)
            else:
                result = await run_agent_loop(model, tool_context=CONTEXT, tool_definitions=definitions)
                observation = result.observations[0]
                if outcome in ('success', 'nonzero'):
                    assert json.loads(observation.result)['exit_code'] == (7 if outcome == 'nonzero' else 0)
                else:
                    assert observation.code in ('tool_execution_failed', 'tool_timeout')
                assert 'PRIVATE' not in str(observation)
            record = owner.sample_scope.journal.records[0]
            assert record.status == {'success': 'completed', 'nonzero': 'completed', 'cleanup': 'unconfirmed',
                                     'cancel': 'cancelled', 'timeout': 'cancelled', 'stale_ready': 'unconfirmed'}[outcome]
            assert owner.command_scope is None
        finally:
            await owner.close()
        scope = owner.task_sample_recovery_store.get(user_id=1, conversation_id='conversation', run_id=10)
        assert scope is owner.sample_scope
        with pytest.raises(SafeToolExecutionError):
            await binding.executor(argv=request().argv)
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['changed_context', 'read_error', 'closed', 'status_error'])
def test_no_fallback_on_context_or_status_failure(lab, monkeypatch, failure):
    async def scenario():
        owner = await make_owner(monkeypatch)
        try:
            if failure == 'status_error':
                def bad(**kwargs):
                    raise OSError('query failed')
                monkeypatch.setattr(owner.sample_bindings, 'read_status', bad)
                with pytest.raises(OSError):
                    await owner.bind_command_tool(CONTEXT)
            else:
                binding = await owner.bind_command_tool(CONTEXT)
                if failure == 'changed_context':
                    monkeypatch.setattr(owners, 'load_tool_execution_context', lambda **kwargs: replace(CONTEXT, task_id='other'))
                elif failure == 'read_error':
                    def bad(**kwargs):
                        raise OSError('PRIVATE')
                    monkeypatch.setattr(owners, 'load_tool_execution_context', bad)
                else:
                    owner._command_closed = True
                with pytest.raises(SafeToolExecutionError):
                    await binding.executor(argv=request().argv)
            assert lab.samples == [] and lab.calls == []
            assert owner.command_scope is None and owner.sample_scope is None
        finally:
            await owner.close()
    asyncio.run(scenario())


def test_close_failure_still_seals_and_preserves_sample_scope(monkeypatch):
    async def scenario():
        owner = await make_owner(monkeypatch)
        scope = owner.task_sample_recovery_store.acquire(user_id=1, conversation_id='conversation', run_id=10)
        owner.sample_scope = scope
        index = scope.journal.reserve(request())
        scope.journal.finish(index, status='unconfirmed')
        class BrokenStream:
            async def aclose(self):
                raise RuntimeError('close failed')
        owner.stream = BrokenStream()
        with pytest.raises(RuntimeError):
            await owner.close()
        assert owner.task_sample_recovery_store.get(user_id=1, conversation_id='conversation', run_id=10) is scope
        from app.services.runtime.sandbox.task_sample_command_journal import TaskSampleJournalUnavailable
        with pytest.raises(TaskSampleJournalUnavailable):
            scope.journal.reserve(request())
        assert scope.journal.records[0].status == 'unconfirmed'
    asyncio.run(scenario())


def test_close_during_capability_read_never_publishes_tool(monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    async def scenario():
        owner = await make_owner(monkeypatch)
        def read(**kwargs):
            entered.set()
            assert release.wait(5)
            return TaskSampleStatus('ready', None)
        monkeypatch.setattr(owner.sample_bindings, 'read_status', read)
        task = asyncio.create_task(owner.bind_command_tool(CONTEXT))
        async def wait():
            while not entered.is_set():
                await asyncio.sleep(0.001)
        try:
            await asyncio.wait_for(wait(), 2)
            owner._command_closed = True
        finally:
            release.set()
        with pytest.raises(SafeToolExecutionError):
            await task
        assert owner.sample_scope is None and owner.command_scope is None
        await owner.close()
    asyncio.run(scenario())
