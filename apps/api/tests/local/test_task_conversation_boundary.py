from app.services.runtime import conversation_execution_scope
"""本地任务会话不能隐式创建：真实 HTTP、缓存边界及删除竞争。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import AgentRun, AgentRunEvent, Conversation, Message, User, Workspace
from app.repositories.chat import conversation_repository as conversations
from app.repositories.runtime import run_repository as runs
from app.services.chat import chat_service
from app.services.runtime.agent_runtime import FinalAnswer
from app.services.tasks.task_deletion_service import TaskRunUnsettledError, delete_workspace_task
from tests.local.test_local_mode import HEADERS
from tests.local import test_local_mode as local_mode_tests
from tests.tasks.test_task_deletion_service import wait_for_database_block

local_client = local_mode_tests.local_client


@pytest.fixture
def lab(local_client, engine, monkeypatch):
    factory = sessionmaker(engine)
    for module in (conversations, runs, conversation_execution_scope):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    prompts = []

    class DecisionMaker:
        def __init__(self, **kwargs):
            prompts.append(deepcopy(kwargs['messages']))

        async def __call__(self, observations):
            return FinalAnswer(content='隔离回复')

    async def completion(**kwargs):
        prompts.append(deepcopy(kwargs['messages']))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='隔离回复'))])

    async def wait(*args):
        await asyncio.Future()

    monkeypatch.setattr(chat_service, 'DeepSeekDecisionMaker', DecisionMaker)
    monkeypatch.setattr(chat_service.client.chat.completions, 'create', AsyncMock(side_effect=completion))
    monkeypatch.setattr(chat_service, 'wait_for_run_cancellation', wait)
    chat_service.conversations.clear()
    workspace_response = local_client.post('/workspaces', headers=HEADERS, json={'name': '本地项目'})
    assert workspace_response.status_code == 201
    workspace = workspace_response.json()
    task_response = local_client.post(f"/workspaces/{workspace['external_id']}/tasks", headers=HEADERS, json={'title': '任务'})
    assert task_response.status_code == 201
    task = task_response.json()
    with Session(engine) as reader:
        owner = reader.scalar(select(Workspace.user_id))
    yield local_client, owner, workspace, task, prompts
    chat_service.conversations.clear()


def counts(engine):
    with Session(engine) as reader:
        return tuple(reader.scalar(select(func.count()).select_from(model))
                     for model in (Conversation, Message, AgentRun, AgentRunEvent))


def delete_task(engine, lab):
    _, owner, workspace, task, _ = lab
    with Session(engine) as session:
        return delete_workspace_task(session, user_id=owner, workspace_id=workspace['external_id'], task_id=task['external_id'])


def invalidate(engine, lab, kind):
    _, _, workspace, task, _ = lab
    session_id = task['conversation_id']
    if kind == 'missing':
        return uuid4().hex
    if kind == 'deleted':
        delete_task(engine, lab)
        return session_id
    with Session(engine) as session, session.begin():
        row = session.scalar(select(Conversation).where(Conversation.external_id == session_id))
        if kind == 'standalone':
            row.task_id = None
        else:
            other = User(external_id=uuid4().hex)
            session.add(other)
            session.flush()
            if kind == 'foreign-conversation':
                row.user_id = other.id
            else:
                session.scalar(select(Workspace).where(Workspace.external_id == workspace['external_id'])).user_id = other.id
    return session_id


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('kind', ['missing', 'deleted', 'standalone', 'foreign-conversation', 'foreign-workspace'])
def test_invalid_local_session_is_404_before_cache_model_or_writes(lab, engine, path, cached, kind):
    client, owner, _, _, prompts = lab
    session_id = invalidate(engine, lab, kind)
    if cached:
        chat_service.conversations[(owner, session_id)] = [{'role': 'system', 'content': '不可信缓存'}]
    before = counts(engine)
    cache_before = deepcopy(chat_service.conversations)
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = client.post(path, headers=HEADERS, json={'session_id': session_id, 'prompt': '不得执行'})
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert response.status_code == 404
    assert response.json() == {'code': 'conversation_not_accessible', 'message': '会话不存在或不可访问'}
    assert response.headers['cache-control'] == 'no-store'
    assert 'x-run-id' not in response.headers
    assert prompts == [] and counts(engine) == before
    assert chat_service.conversations == cache_before
    assert not any(sql.lstrip().upper().startswith('INSERT INTO CONVERSATIONS') for sql in statements)


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_existing_task_continues_and_reloads_history(lab, engine, path):
    client, _, _, task, prompts = lab
    for prompt in ['第一轮', '第二轮']:
        response = client.post(path, headers=HEADERS, json={'session_id': task['conversation_id'], 'prompt': prompt})
        assert response.status_code == 200
        if path.endswith('stream'):
            assert '"RUN_FINISHED"' in response.text
        # 模拟进程缓存丢失，下一轮必须读取持久化历史。
        chat_service.conversations.clear()
    assert [message['content'] for message in prompts[-1]][1:] == ['第一轮', '隔离回复', '第二轮']
    assert counts(engine)[:2] == (1, 4)
    assert counts(engine)[2] == (2 if path.endswith('stream') else 0)


@pytest.mark.parametrize('kind', ['missing', 'deleted', 'standalone', 'foreign-conversation', 'foreign-workspace'])
@pytest.mark.parametrize('operation', ['ensure', 'load', 'save'])
def test_repository_entrypoints_reject_without_recreating(lab, engine, kind, operation):
    _, owner, _, _, _ = lab
    session_id = invalidate(engine, lab, kind)
    before = counts(engine)
    with pytest.raises(conversations.ConversationNotAccessibleError):
        if operation == 'ensure':
            conversations.ensure_owned_conversation(user_id=owner, session_id=session_id)
        elif operation == 'load':
            conversations.load_conversation(user_id=owner, session_id=session_id)
        else:
            conversations.save_conversation_turn(user_id=owner, session_id=session_id, user_content='bad', assistant_content='bad')
    assert counts(engine) == before


def test_short_authorization_transaction_releases_lock_before_model(lab, engine, monkeypatch):
    client, _, _, task, _ = lab
    async def completion(**kwargs):
        # 模型等待前授权事务已释放；另一个连接可立即取得会话锁。
        with Session(engine) as session, session.begin():
            session.execute(text("SET LOCAL lock_timeout = '300ms'"))
            row = session.scalar(select(Conversation).where(Conversation.external_id == task['conversation_id']).with_for_update(nowait=True))
            assert row is not None
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='回复'))])
    monkeypatch.setattr(chat_service.client.chat.completions, 'create', completion)
    response = client.post('/chat', headers=HEADERS, json={'session_id': task['conversation_id'], 'prompt': '验证锁释放'})
    assert response.status_code == 200


@pytest.mark.parametrize('winner', ['delete', 'run'])
def test_delete_and_run_creation_serialize_without_recreation(lab, engine, monkeypatch, winner):
    _, owner, _, task, _ = lab
    locked, release, waiting = Event(), Event(), Event()
    waiter_pid = []
    original = runs.get_or_create_owned_conversation

    def lookup(session, **kwargs):
        session.execute(text("SET LOCAL statement_timeout = '8s'"))
        if winner == 'delete':
            waiter_pid.append(session.scalar(text('SELECT pg_backend_pid()')))
            waiting.set()
        row = original(session, **kwargs)
        if winner == 'run':
            locked.set()
            assert release.wait(8)
        return row

    def observe(connection, cursor, statement, parameters, context, executemany):
        if winner == 'delete' and statement.startswith('SELECT agent_runs.id'):
            locked.set()
            assert release.wait(8)
        elif winner == 'run' and statement.startswith('SELECT conversations.') and 'FOR UPDATE' in statement:
            # 仅删除线程在 run 已取得锁之后启动，首次 run 查询不记录。
            if locked.is_set():
                waiter_pid.append(connection.scalar(text('SELECT pg_backend_pid()')))
                waiting.set()

    monkeypatch.setattr(runs, 'get_or_create_owned_conversation', lookup)
    def create_run():
        return runs.create_agent_run(user_id=owner, session_id=task['conversation_id'], prompt='竞争')
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(delete_task, engine, lab) if winner == 'delete' else pool.submit(create_run)
            try:
                assert locked.wait(5)
                second = pool.submit(create_run) if winner == 'delete' else pool.submit(delete_task, engine, lab)
                assert waiting.wait(5)
                wait_for_database_block(engine, waiter_pid[0])
            finally:
                release.set()
            first.result(timeout=10)
            error = conversations.ConversationNotAccessibleError if winner == 'delete' else TaskRunUnsettledError
            with pytest.raises(error):
                second.result(timeout=10)
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert counts(engine) == ((0, 0, 0, 0) if winner == 'delete' else (1, 0, 1, 1))
