"""请求能力快照、上下文门禁、受控模型往返与同步工作线程。"""

import asyncio
import json
from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request

from app.routers.chat import chat_execution as owners
from app.schemas import ChatRequest
from app.services.model.model_decision import DeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import run_agent_loop
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.workspace.git.application_samples import start_git_samples, stop_git_samples, get_git_samples
from app.services.workspace.git.status_parser import GitStatusSnapshot
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError, tools_for_execution
from tests.model.test_model_decision import build_text_response, build_tool_response

CONTEXT = ToolExecutionContext(1, 'conversation', 'workspace', 'task')


@pytest.fixture
def owner(monkeypatch):
    application = FastAPI()
    start_git_samples(application)
    request = Request({'type': 'http', 'app': application})
    execution = owners.ChatExecution(
        user_id=1, body=ChatRequest(session_id='conversation', prompt='test'), threads=ExecutionThreads(),
        git_samples_provider=lambda: get_git_samples(request),
    )
    monkeypatch.setattr(owners, 'load_tool_execution_context', lambda **kwargs: CONTEXT)
    yield execution, get_git_samples(request), application
    asyncio.run(stop_git_samples(application))


@pytest.mark.parametrize('context', [None, {}, replace(CONTEXT, user_id=2), replace(CONTEXT, conversation_id='other')])
def test_owner_rejects_untrusted_context(owner, context):
    execution, manager, _ = owner
    with pytest.raises(SafeToolExecutionError):
        execution.bind_git_status_tool(context)
    assert manager._bindings == {}


@pytest.mark.parametrize('kind', ['missing', 'stopped', 'closed-request'])
def test_missing_or_stopped_owner_refuses_binding(owner, kind):
    execution, manager, application = owner
    if kind == 'missing':
        execution.git_samples_provider = None
    elif kind == 'stopped':
        asyncio.run(stop_git_samples(application))
    else:
        execution._command_closed = True
    with pytest.raises(SafeToolExecutionError):
        execution.bind_git_status_tool(CONTEXT)
    assert manager._bindings == {}


def test_schema_and_bound_context_without_global_mutation(owner):
    execution, _, _ = owner
    executor = execution.bind_git_status_tool(CONTEXT)
    for context in (None, {}):
        with pytest.raises(ToolContextRequiredError):
            tools_for_execution(context=context, git_status_executor=executor)
    tools = tools_for_execution(context=CONTEXT, git_status_executor=executor)
    tool = next(tool for tool in tools if tool.name == 'git_sample_status')
    schema = tool.as_model_tool()['function']['parameters']
    assert schema['properties'] == {} and schema['additionalProperties'] is False
    with pytest.raises(ToolContextRequiredError):
        tool.execute(tool.validate_arguments('{}'), context=replace(CONTEXT))
    assert 'git_sample_status' not in TOOL_REGISTRY
    assert 'git_sample_status' not in {tool.name for tool in tools_for_execution(context=CONTEXT)}


@pytest.mark.parametrize('kind', ['success', 'missing', 'invalid', 'private-error', 'moved', 'closed-request', 'cancel', 'closed-app'])
def test_model_roundtrip_and_runtime_boundary(owner, monkeypatch, kind):
    execution, manager, application = owner
    calls = []
    main_thread = get_ident()
    def read(**kwargs):
        assert get_ident() != main_thread
        calls.append(kwargs)
        if kind == 'private-error':
            raise OSError('PRIVATE_PATH')
        if kind == 'cancel':
            raise asyncio.CancelledError()
        return GitStatusSnapshot((), 0)
    if kind not in ('missing', 'closed-app'):
        monkeypatch.setattr(manager, 'read_status', read)
    definitions = tools_for_execution(context=CONTEXT, git_status_executor=execution.bind_git_status_tool(CONTEXT))
    if kind == 'moved':
        monkeypatch.setattr(owners, 'load_tool_execution_context', lambda **kwargs: replace(CONTEXT, task_id='other'))
    elif kind == 'closed-request':
        execution._command_closed = True
    elif kind == 'closed-app':
        asyncio.run(stop_git_samples(application))
    turns = []
    async def create(**kwargs):
        turns.append(kwargs)
        assert kwargs['tools'] == [tool.as_model_tool() for tool in definitions]
        if len(turns) == 1:
            return build_tool_response(('git', 'git_sample_status', '{"path":"PRIVATE_PATH"}' if kind == 'invalid' else '{}'))
        raw = next(message['content'] for message in kwargs['messages'] if message['role'] == 'tool')
        assert 'PRIVATE_PATH' not in raw
        if kind == 'success':
            assert json.loads(raw)['source'] == 'task_git_sample'
        return build_text_response('样例状态已处理')
    model = DeepSeekDecisionMaker(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model='test', messages=[{'role': 'user', 'content': 'read sample'}],
        tool_context=CONTEXT, tool_definitions=definitions,
    )
    async def scenario():
        try:
            return await run_agent_loop(model, tool_context=CONTEXT, tool_definitions=definitions,
                                        execution_threads=execution.threads)
        finally:
            await execution.threads.wait_closed()
    if kind == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scenario())
        assert len(turns) == 1
    else:
        result = asyncio.run(scenario())
        observation = result.observations[0]
        if kind != 'success':
            assert observation.code == ('invalid_tool_arguments' if kind == 'invalid' else 'tool_execution_failed')
            if kind != 'invalid':
                assert observation.details == {
                    'missing': 'task_git_sample_unavailable', 'closed-app': 'task_git_sample_unavailable',
                    'moved': 'workspace_not_accessible', 'closed-request': 'task_git_sample_unavailable',
                    'private-error': 'git_status_unavailable',
                }[kind]
        assert len(turns) == 2
    assert len(calls) == (1 if kind in ('success', 'private-error', 'cancel') else 0)
    assert not manager._bindings


@pytest.mark.parametrize('outcome', ['timeout', 'cancel'])
def test_inflight_thread_stays_tracked_after_wait_ends(owner, monkeypatch, outcome):
    from threading import Event
    execution, manager, _ = owner
    entered, release, finished = Event(), Event(), Event()
    def read(**kwargs):
        entered.set()
        assert release.wait(5)
        finished.set()
        return GitStatusSnapshot((), 0)
    monkeypatch.setattr(manager, 'read_status', read)
    definitions = tools_for_execution(context=CONTEXT, git_status_executor=execution.bind_git_status_tool(CONTEXT))
    definitions = tuple(replace(tool, timeout_seconds=0.05) if tool.name == 'git_sample_status' else tool for tool in definitions)
    turns = []
    async def create(**kwargs):
        turns.append(kwargs)
        if len(turns) == 1:
            return build_tool_response(('git', 'git_sample_status', '{}'))
        return build_text_response('未确认完成')
    maker = DeepSeekDecisionMaker(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model='test', messages=[{'role': 'user', 'content': 'read'}],
        tool_context=CONTEXT, tool_definitions=definitions,
    )
    async def scenario():
        task = asyncio.create_task(run_agent_loop(maker, tool_context=CONTEXT, tool_definitions=definitions,
                                                 execution_threads=execution.threads))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if outcome == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result.observations[0].code == 'tool_timeout'
            assert not finished.is_set()
            drain = asyncio.create_task(execution.threads.wait_closed())
            await asyncio.sleep(0)
            assert not drain.done()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await execution.threads.wait_closed()
        assert finished.is_set()
        await drain
    asyncio.run(scenario())


def test_dependency_uses_request_application_manager(monkeypatch):
    from contextlib import asynccontextmanager
    from app.services.runtime.execution.command_recovery_store import CommandRecoveryStore
    from app.services.runtime.execution.execution_budget import ExecutionBudget
    application = FastAPI()
    start_git_samples(application)
    application.state.execution_budget = ExecutionBudget(capacity=1)
    application.state.command_recovery_store = CommandRecoveryStore()
    request = Request({'type': 'http', 'app': application})
    monkeypatch.setattr(owners.settings, 'app_mode', 'local')
    monkeypatch.setattr(owners, '_check_local_conversation', lambda **kwargs: None)
    @asynccontextmanager
    async def scope(**kwargs):
        threads = ExecutionThreads()
        try:
            yield threads
        finally:
            await threads.wait_closed()
    monkeypatch.setattr(owners, 'conversation_execution', scope)
    async def scenario():
        dependency = owners.require_chat_execution(
            request=request, body=ChatRequest(session_id='conversation', prompt='test'),
            current_user=SimpleNamespace(id=1),
        )
        try:
            execution = await anext(dependency)
            assert execution.git_samples_provider() is get_git_samples(request)
        finally:
            await dependency.aclose()
            await stop_git_samples(application)
        assert application.state.execution_budget.in_use == 0
    asyncio.run(scenario())
