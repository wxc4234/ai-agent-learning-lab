"""应用manager→请求能力→受控模型→真实Git/隔离PostgreSQL→事件持久化。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import AgentRun, AgentRunEvent, Message, Workspace
from app.repositories.chat import conversation_repository
from app.repositories.runtime import run_repository
from app.routers.chat import chat as route
from app.routers.chat.chat_execution import ChatExecution
from app.schemas import ChatRequest
from app.services.chat import chat_service
from app.services.runtime.agent import tool_execution_context
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.workspace.git import task_git_samples
from app.services.workspace.git.application_samples import start_git_samples, stop_git_samples, get_git_samples
from tests.chat.test_chat_tool_context import isolated_history_and_monitor as isolated_history_and_monitor  # noqa: PLC0414
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.model.response_stream import build_text_response, build_tool_response
from tests.assertions import require_value
from tests.workspace.git.test_diff_capture import baseline
from tests.workspace.git.test_status_capture import git, snapshot


@pytest.mark.parametrize('kind', ['worktree', 'staged', 'missing', 'invalid', 'manager-missing', 'missing-head'])
def test_route_diff_events_and_unchanged_project(engine, target, monkeypatch, kind):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(settings, 'agent_max_total_tokens', None)
    factory = sessionmaker(bind=engine)
    for module in (conversation_repository, run_repository, tool_execution_context, task_git_samples):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    application = FastAPI()
    request = Request({'type': 'http', 'app': application})
    if kind != 'manager-missing':
        start_git_samples(application)
    manager = None if kind == 'manager-missing' else get_git_samples(request)
    scope = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    path = None
    if kind in ('worktree', 'staged', 'invalid', 'missing-head'):
        require_value(manager).bind(**scope)
        root = require_value(manager)._bindings[tuple(scope.values())].sample.root
        handle = require_value(manager)._bindings[tuple(scope.values())].sample
        if kind != 'missing-head':
            baseline(handle)
            path = root / 'value.txt'
            path.write_bytes(b'value = 2\n')
            git(handle, 'add', 'value.txt')
            path.write_bytes(b'value = 3\n')
            before = snapshot(root)
    with Session(engine) as session:
        original_root = session.scalar(select(Workspace.root_path))
    create = AsyncMock(side_effect=[
        build_tool_response(('git', 'git_sample_diff', '{"directory":"PRIVATE"}' if kind == 'invalid' else json.dumps({'scope': 'staged' if kind in ('staged', 'missing-head') else 'worktree'}))),
        build_text_response('仅查询临时Git样例'),
    ])
    monkeypatch.setattr(chat_service, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    owner = ChatExecution(
        user_id=target['user_id'], body=ChatRequest(session_id=target['conversation_id'], prompt='查询样例'),
        threads=ExecutionThreads(), git_samples_provider=lambda: get_git_samples(request),
    )
    # 此专项只装配Git；命令能力另有独立回归，不触发Docker。
    monkeypatch.setattr(owner, 'bind_command_tool', AsyncMock(return_value=None))
    async def scenario():
        try:
            response = await route.chat_stream(owner)
            events = [json.loads(line if isinstance(line, str) else bytes(line)) async for line in response.body_iterator]
            expected = 'RUN_ERROR' if kind == 'manager-missing' else 'RUN_FINISHED'
            assert events[-1]['type'] == expected
            if path is not None:
                assert snapshot(root) == before
            return require_value(owner.creation).result(), events
        finally:
            await owner.close()
            await stop_git_samples(application)
    run_id, events = asyncio.run(scenario())
    if kind == 'manager-missing':
        assert create.call_count == 0
    else:
        assert create.call_count == 2
        kwargs = create.call_args.kwargs
        schema = next(tool['function']['parameters'] for tool in kwargs['tools'] if tool['function']['name'] == 'git_sample_diff')
        assert schema['properties']['scope']['enum'] == ['worktree', 'staged']
        assert schema['additionalProperties'] is False
        raw = next(message['content'] for message in kwargs['messages'] if message['role'] == 'tool')
        assert 'PRIVATE' not in raw
        result = json.loads(raw)
        if kind in ('worktree', 'staged'):
            assert result['source'] == 'task_git_sample'
            assert result['scope'] == kind
            assert ('-value = 2\n+value = 3\n' if kind == 'worktree' else '-value = 1\n+value = 2\n') in result['diff']
            assert str(root) not in raw
        elif kind == 'missing':
            assert result['error']['details'] == 'task_git_sample_unavailable'
            assert not require_value(manager)._bindings
        elif kind == 'missing-head':
            assert result['error']['details'] == 'git_diff_command_failed'
        else:
            assert result['error']['code'] == 'invalid_tool_arguments'
        expected_event = 'TOOL_CALL_RESULT' if kind in ('worktree', 'staged') else 'TOOL_CALL_ERROR'
        assert sum(event['type'] == expected_event for event in events) == 1
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) == original_root
        assert require_value(session.get(AgentRun, run_id)).status == ('error' if kind == 'manager-missing' else 'done')
        stored = session.scalars(select(AgentRunEvent.event_type).where(AgentRunEvent.run_id == run_id)).all()
        assert ('RUN_ERROR' if kind == 'manager-missing' else expected_event) in stored

        if kind != 'manager-missing':
            persisted = require_value(session.scalar(select(AgentRunEvent).where(
                AgentRunEvent.run_id == run_id, AgentRunEvent.event_type == expected_event,
            )))
            assert persisted.payload['tool_name'] == 'git_sample_diff'
            if kind in ('worktree', 'staged'):
                assert persisted.payload['result'] == raw
            else:
                assert persisted.payload['code'] == result['error']['code']
            replies = session.scalars(select(Message.content).where(
                Message.conversation_id == target['conversation_pk'], Message.role == 'assistant',
            )).all()
            assert replies == ['仅查询临时Git样例']
