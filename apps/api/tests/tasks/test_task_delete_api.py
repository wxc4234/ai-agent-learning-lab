"""任务删除 HTTP：真实数据库事实、拒绝边界与提交后响应失败。"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import AgentRun, AgentRunEvent, Conversation, ConversationExecutionSlot, Message, Task, User, Workspace
from app.routers.workspace import tasks as workspace
from tests.local.test_local_mode import HEADERS
from tests.tasks.test_task_workspace import task
import tests.workspace.directory.test_workspace_binding_api as binding

local_client = binding.local_client
target = binding.target


def assert_stored(engine, created, exists=True):
    # 新连接检查提交事实，避免只观察 Session 缓存。
    with Session(engine) as reader:
        assert (reader.scalar(select(Task).where(Task.external_id == created['external_id'])) is not None) is exists
        assert (reader.scalar(select(Conversation).where(Conversation.external_id == created['conversation_id'])) is not None) is exists


@pytest.mark.parametrize('content_type', [None, 'application/json', 'text/plain'])
def test_empty_request_returns_empty_204_and_preserves_project(local_client, target, engine, content_type):
    created, path = task(local_client, target)
    sibling, _ = task(local_client, target)
    binding.safe(binding.put(local_client, target), 200)
    headers = HEADERS | {'Cookie': 'agent_session=forged', 'X-User-ID': '999'}
    if content_type:
        headers['Content-Type'] = content_type
    response = local_client.delete(path, headers=headers)
    binding.safe(response, 204)
    assert response.content == b''
    assert 'content-type' not in response.headers
    assert_stored(engine, created, False)
    assert_stored(engine, sibling)
    assert binding.stored(engine, target[0]) == target[1]
    binding.safe(local_client.get(path, headers=HEADERS), 404, 'workspace_not_accessible')
    binding.safe(local_client.delete(path, headers=HEADERS), 404, 'workspace_not_accessible')


@pytest.mark.parametrize('body', [b'{}', b'null', b' ', b'\n', b'{PRIVATE', b'{"user_id":1}'])
def test_any_body_rejected_before_identity(local_client, target, engine, monkeypatch, body):
    created, path = task(local_client, target)
    def forbidden():
        pytest.fail('body rejection must precede identity')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    response = local_client.request('DELETE', path, headers=HEADERS, content=body)
    binding.safe(response, 422, 'invalid_task_input')
    assert_stored(engine, created)


@pytest.mark.parametrize('headers,code', [
    ({}, 'local_access_rejected'),
    (HEADERS | {'X-Local-Runtime-Token': 'bad'}, 'local_access_rejected'),
    (HEADERS | {'Host': 'evil.test'}, 'local_access_rejected'),
    (HEADERS | {'Origin': 'http://localhost:3000.evil.test'}, 'local_access_rejected'),
    ({'X-Local-Runtime-Token': 'a' * 64}, 'workspace_origin_rejected'),
])
def test_access_rejected_before_identity(local_client, target, engine, monkeypatch, headers, code):
    created, path = task(local_client, target)
    def forbidden():
        pytest.fail('access boundary must precede identity')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    binding.safe(local_client.delete(path, headers=headers), 403, code)
    assert_stored(engine, created)


def test_nonlocal_rejected_before_identity(local_client, target, monkeypatch, engine):
    created, path = task(local_client, target)
    monkeypatch.setattr(settings, 'app_mode', 'account')
    def forbidden():
        pytest.fail('nonlocal task must not resolve identity')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    binding.safe(local_client.delete(path, headers=HEADERS), 403, 'local_mode_required')
    assert_stored(engine, created)


@pytest.mark.parametrize('field', ['workspace', 'task'])
@pytest.mark.parametrize('identifier', ['bad', 'A' * 32, 'a' * 31, 'a' * 33, 'g' * 32])
def test_invalid_path_never_calls_service(local_client, target, engine, monkeypatch, field, identifier):
    created, path = task(local_client, target)
    path = path.replace(target[0] if field == 'workspace' else created['external_id'], identifier)
    def forbidden(**kwargs):
        pytest.fail('invalid path must not invoke deletion')
    monkeypatch.setattr(workspace, 'delete_workspace_task', forbidden)
    binding.safe(local_client.delete(path, headers=HEADERS), 422, 'invalid_task_input')
    assert_stored(engine, created)


@pytest.mark.parametrize('kind', ['missing-task', 'missing-workspace', 'wrong-project', 'foreign-project', 'foreign-conversation'])
def test_inaccessible_resources_share_404(local_client, target, engine, kind):
    created, path = task(local_client, target)
    if kind == 'missing-task':
        path = path.replace(created['external_id'], 'f' * 32)
    elif kind == 'missing-workspace':
        path = path.replace(target[0], 'f' * 32)
    elif kind == 'wrong-project':
        other = local_client.post('/workspaces', headers=HEADERS, json={'name': '另一项目'})
        assert other.status_code == 201
        path = path.replace(target[0], other.json()['external_id'])
    else:
        with Session(engine) as session, session.begin():
            other = User(external_id='foreign-owner')
            session.add(other)
            session.flush()
            model, identifier = (Workspace, target[0]) if kind == 'foreign-project' else (Conversation, created['conversation_id'])
            session.scalar(select(model).where(model.external_id == identifier)).user_id = other.id
    binding.safe(local_client.delete(path, headers=HEADERS), 404, 'workspace_not_accessible')
    assert_stored(engine, created)


@pytest.mark.parametrize('kind', ['running', 'done', 'error', 'aborted', 'unknown'])
def test_history_conflict_preserves_all_records(local_client, target, engine, kind):
    created, path = task(local_client, target)
    with Session(engine) as session, session.begin():
        conversation_id = session.scalar(select(Conversation.id).where(Conversation.external_id == created['conversation_id']))
        if kind == 'message':
            session.add(Message(conversation_id=conversation_id, role='user', content='PRIVATE'))
        else:
            run = AgentRun(conversation_id=conversation_id, status=kind)
            session.add(run)
            session.flush()
            session.add(AgentRunEvent(run_id=run.id, event_type='PRESERVE', payload={'content': 'PRIVATE'}))
    binding.safe(local_client.delete(path, headers=HEADERS), 409, 'task_run_unsettled')
    assert_stored(engine, created)
    with Session(engine) as reader:
        if kind == 'message':
            assert reader.scalar(select(Message.content)) == 'PRIVATE'
        else:
            assert reader.scalar(select(AgentRun.status)) == kind
            assert reader.scalar(select(AgentRunEvent.payload)) == {'content': 'PRIVATE'}


@pytest.mark.parametrize('failure', [None, 'before-service', 'second-delete', 'after-commit', 'response'])
def test_session_lifecycle_and_uncertain_response(local_client, target, engine, monkeypatch, caplog, failure):
    created, path = task(local_client, target)
    sessions = []
    class TrackedSession(Session):
        closed = False
        def close(self):
            super().close()
            self.closed = True
        def execute(self, statement, *args, **kwargs):
            if failure == 'second-delete' and getattr(statement, 'is_delete', False) and statement.table.name == 'tasks':
                assert self.connection().scalar(select(Conversation.id).where(Conversation.external_id == created['conversation_id'])) is None
                self.connection().execute(text('SELECT * FROM missing_PRIVATE_table'))
            return super().execute(statement, *args, **kwargs)
    def factory():
        session = TrackedSession(engine)
        sessions.append(session)
        return session
    monkeypatch.setattr(dependencies, 'SessionLocal', factory)
    monkeypatch.setattr(workspace, 'SessionLocal', factory)
    original = workspace.delete_workspace_task
    def checked(**kwargs):
        assert sessions[0].closed
        assert not kwargs['session'].in_transaction()
        if failure == 'before-service':
            raise RuntimeError('PRIVATE')
        result = original(**kwargs)
        if failure == 'after-commit':
            raise RuntimeError('PRIVATE after commit')
        return result
    monkeypatch.setattr(workspace, 'delete_workspace_task', checked)
    if failure == 'response':
        def broken(**kwargs):
            assert all(session.closed for session in sessions)
            raise RuntimeError('PRIVATE response')
        monkeypatch.setattr(workspace, 'Response', broken)
    response = local_client.delete(path, headers=HEADERS)
    binding.safe(response, 204 if failure is None else 500, None if failure is None else 'task_deletion_uncertain')
    assert len(sessions) == 2 and all(session.closed for session in sessions)
    assert_stored(engine, created, failure in ('before-service', 'second-delete'))
    assert 'PRIVATE' not in response.text + caplog.text
    if failure:
        assert '未确认' in response.json()['message']


def test_openapi_declares_no_body_and_empty_success(local_client):
    operation = local_client.get('/openapi.json', headers=HEADERS).json()['paths']['/workspaces/{workspace_id}/tasks/{task_id}']['delete']
    assert 'requestBody' not in operation
    assert 'content' not in operation['responses']['204']
    assert {'403', '404', '409', '422', '500'} <= operation['responses'].keys()


@pytest.mark.parametrize('status', [None, 'running', 'done', 'error', 'aborted'])
def test_execution_slot_returns_safe_conflict_and_preserves_task(local_client, target, engine, status):
    created, path = task(local_client, target)
    with Session(engine) as session, session.begin():
        conversation = session.scalar(select(Conversation).where(Conversation.external_id == created['conversation_id']))
        conversation_pk = conversation.id
        session.add(ConversationExecutionSlot(conversation_id=conversation_pk, owner_token='a' * 32))
        if status is not None:
            session.add(AgentRun(conversation_id=conversation_pk, status=status))
    response = local_client.delete(path, headers=HEADERS)
    binding.safe(response, 409, 'conversation_busy')
    assert response.json() == {
        'code': 'conversation_busy',
        'message': '该任务仍有执行占用，请等待执行及收尾完成后重试',
    }
    assert_stored(engine, created)
    with Session(engine) as session, session.begin():
        slot = session.get(ConversationExecutionSlot, conversation_pk)
        assert slot.owner_token == 'a' * 32
        session.delete(slot)
    # 占用消失后仍需结束证据：空任务可删，这些缺 finished_at 的运行继续拒绝。
    response = local_client.delete(path, headers=HEADERS)
    if status is None:
        binding.safe(response, 204)
        assert_stored(engine, created, False)
    else:
        binding.safe(response, 409, 'task_run_unsettled')
        assert_stored(engine, created)
