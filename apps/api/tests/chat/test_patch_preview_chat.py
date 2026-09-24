"""真实授权文件与隔离 PostgreSQL 的补丁聊天事件链，模型受控。"""

import asyncio
import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import AgentRun, AgentRunEvent, FileEditProposal, Message, Workspace
from app.repositories.chat import conversation_repository
from app.repositories.runtime import run_repository
from app.services.chat import chat_service as service
from app.services.runtime.agent import tool_execution_context
from app.services.workspace.directory import workspace_path
from tests.chat import test_chat_tool_context as chat_tests
from tests.model.test_model_decision import build_text_response, build_tool_response


target = chat_tests.target
isolated_history_and_monitor = chat_tests.isolated_history_and_monitor


@pytest.mark.parametrize('kind', ['success', 'conflict', 'unbound'])
def test_chat_patch_events_and_persistence(engine, target, tmp_path, monkeypatch, kind):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(settings, 'agent_max_total_tokens', None)
    factory = sessionmaker(bind=engine)
    for module in (conversation_repository, run_repository, tool_execution_context, workspace_path):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    root = tmp_path.resolve()
    path = root / 'file.txt'
    original = b'old\n'
    path.write_bytes(original)
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = None if kind == 'unbound' else str(root)
    old = 'wrong' if kind == 'conflict' else 'old'
    args = {'relative_path': 'file.txt', 'patch': f'--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-{old}\n+new\n'}
    create = AsyncMock(side_effect=[build_tool_response(('patch', 'preview_file_patch', json.dumps(args))), build_text_response('补丁仅供预览')])
    monkeypatch.setattr(service, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    run_id = run_repository.create_agent_run(user_id=target['user_id'], session_id=target['conversation_id'])
    events = asyncio.run(chat_tests.collect(target['user_id'], target['conversation_id'], run_id))
    assert events[-1]['type'] == 'RUN_FINISHED'
    assert path.read_bytes() == original
    request = create.call_args.kwargs
    tool = next(item['function'] for item in request['tools'] if item['function']['name'] == 'preview_file_patch')
    assert set(tool['parameters']['properties']) == {'relative_path', 'patch'}
    observation = next(message['content'] for message in request['messages'] if message['role'] == 'tool')
    assert str(root) not in observation and 'updated_content' not in observation
    public = json.loads(observation)
    expected = 'TOOL_CALL_RESULT' if kind == 'success' else 'TOOL_CALL_ERROR'
    assert sum(event['type'] == expected for event in events) == 1
    if kind == 'success':
        assert public['status'] == 'preview_only'
        assert public['baseline_sha256'] == sha256(original).hexdigest()
        assert public['diff_truncated'] is False
    else:
        assert public['error']['details'] == ('patch_context_mismatch' if kind == 'conflict' else 'workspace_directory_unbound')
    with Session(engine) as session:
        assert session.get(AgentRun, run_id).status == 'done'
        stored = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id)).all()
        assert sum(event.event_type == expected for event in stored) == 1
        messages = session.scalars(select(Message).where(Message.conversation_id == target['conversation_pk']).order_by(Message.id)).all()
        assert [(message.role, message.content) for message in messages] == [('user', 'read'), ('assistant', '补丁仅供预览')]
        assert session.scalar(select(func.count()).select_from(FileEditProposal)) == 0
