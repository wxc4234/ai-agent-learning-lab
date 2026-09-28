"""受控模型、真实Git与隔离PostgreSQL的工具往返，不调用Docker。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import AgentRunEvent, Message
from app.repositories.chat import conversation_repository
from app.repositories.runtime import run_repository
from app.routers.chat import chat as route
from app.routers.chat.chat_execution import ChatExecution
from app.schemas import ChatRequest
from app.services.chat import chat_service
from app.services.runtime.execution.execution_threads import ExecutionThreads
from tests.chat.test_chat_tool_context import isolated_history_and_monitor as isolated_history_and_monitor  # noqa: PLC0414
from tests.assertions import require_value
from tests.model.response_stream import build_tool_response, build_text_response
from tests.workspace.git.test_task_sample_diff import loop, sample, setup, target, fingerprint

__all__ = ['loop', 'sample', 'setup', 'target']


@pytest.mark.parametrize('mode', ['success', 'empty', 'invalid', 'sealed', 'stale', 'query_error'])
def test_chat(loop, target, engine, monkeypatch, mode):
    bindings, _scope, root, _context = loop
    monkeypatch.setattr(settings, 'agent_max_total_tokens', None)
    factory = sessionmaker(bind=engine)
    for module in (conversation_repository, run_repository):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    if mode != 'empty':
        (root / 'example.txt').write_bytes(b'new\n')
    before = fingerprint(root / 'example.txt')
    if mode == 'sealed':
        next(iter(bindings._bindings.values())).state = 'uncertain'
    if mode == 'query_error':
        def fail(**kwargs):
            raise OSError('PRIVATE')
        monkeypatch.setattr(bindings, 'read_status', fail)
    answers = [build_tool_response(('diff', 'read_task_sample_diff', '{"path":"/secret"}' if mode == 'invalid' else '{}')), build_text_response('样例差异已处理')]
    async def respond(**kwargs):
        # 能力展示后来源失效，实际服务仍应阻止Git执行。
        if mode == 'stale':
            next(iter(bindings._bindings.values())).state = 'uncertain'
        return answers.pop(0)
    create = AsyncMock(side_effect=respond)
    monkeypatch.setattr(chat_service, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    execution = ChatExecution(user_id=target['user_id'], body=ChatRequest(session_id=target['conversation_id'], prompt='查看样例差异'), threads=ExecutionThreads(), sample_bindings=bindings)
    monkeypatch.setattr(execution, 'bind_command_tool', AsyncMock(return_value=None))
    monkeypatch.setattr(execution, 'bind_verification_tool', AsyncMock(return_value=None))
    monkeypatch.setattr(execution, 'bind_git_status_tool', lambda ctx: None)
    monkeypatch.setattr(execution, 'bind_git_diff_tool', lambda ctx: None)
    async def scenario():
        try:
            response = await route.chat_stream(execution)
            events = [json.loads(line if isinstance(line, str) else bytes(line)) async for line in response.body_iterator]
            return require_value(execution.creation).result(), events
        finally:
            await execution.close()
    run_id, events = asyncio.run(scenario())
    if mode == 'query_error':
        assert create.call_count == 0 and events[-1]['type'] == 'RUN_ERROR'
    else:
        assert create.call_count == 2 and events[-1]['type'] == 'RUN_FINISHED'
        offered = {item['function']['name'] for item in create.call_args.kwargs['tools']}
        assert ('read_task_sample_diff' in offered) == (mode != 'sealed')
        raw = next(m['content'] for m in create.call_args.kwargs['messages'] if m['role'] == 'tool')
        assert str(root) not in raw and 'PRIVATE' not in raw
        kind = 'TOOL_CALL_RESULT' if mode in ('success', 'empty') else 'TOOL_CALL_ERROR'
        assert sum(e['type'] == kind for e in events) == 1
        with Session(engine) as session:
            event = require_value(session.scalar(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id, AgentRunEvent.event_type == kind)))
            assert event.payload['tool_name'] == 'read_task_sample_diff'
            if kind == 'TOOL_CALL_RESULT':
                assert event.payload['result'] == raw
                assert bool(json.loads(raw)['diff']) == (mode == 'success')
            assert session.scalars(select(Message.content).where(Message.role == 'assistant')).all() == ['样例差异已处理']
    assert fingerprint(root / 'example.txt') == before
