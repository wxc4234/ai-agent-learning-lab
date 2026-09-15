"""Task 创建 HTTP：真实 app 与隔离 PostgreSQL，覆盖提交后的响应失败。"""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import Conversation, Task, User, Workspace
from app.routers.workspace import workspace
import tests.workspace.test_workspace_binding_api as binding
from tests.local.test_local_mode import HEADERS

local_client = binding.local_client
target = binding.target


def post(client, target, body=None, headers=None):
    return client.post(
        f'/workspaces/{target[0]}/tasks',
        headers=HEADERS if headers is None else headers,
        json={'title': '新任务'} if body is None else body,
    )


def counts(engine):
    with Session(engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in (Task, Conversation))


def test_success_normalizes_commits_and_keeps_identity_server_owned(local_client, target, engine):
    response = post(local_client, target, {'title': ' \t新任务\n'}, HEADERS | {'Cookie': 'agent_session=forged', 'X-User-ID': '999999'})
    binding.safe(response, 201)
    payload = response.json()
    assert set(payload) == {'external_id', 'workspace_id', 'conversation_id', 'title', 'created_at'}
    assert payload['title'] == '新任务'
    assert payload['workspace_id'] == target[0]
    with Session(engine) as session:
        task = session.scalar(select(Task))
        conversation = session.scalar(select(Conversation))
        assert task.external_id == payload['external_id']
        assert conversation.external_id == payload['conversation_id']
        assert task.conversation is conversation
        assert conversation.user_id == task.workspace.user_id
        assert task.workspace.root_path is None
    # 同标题不是幂等键，重复调用确实创建不同资源。
    second = post(local_client, target, {'title': '新任务'})
    binding.safe(second, 201)
    assert second.json()['external_id'] != payload['external_id']
    assert counts(engine) == (2, 2)


@pytest.mark.parametrize('body', [{}, [], {'title': None}, {'title': 1}, {'title': True}, {'title': []}, {'title': '任务', 'user_id': 1}, {'title': '任务', 'conversation_id': 'PRIVATE'}])
def test_strict_input(local_client, target, engine, body):
    binding.safe(post(local_client, target, body), 422, 'invalid_task_input')
    assert counts(engine) == (0, 0)


@pytest.mark.parametrize('title', ['', ' \t\n\u3000', '😀' * 201])
def test_title_errors(local_client, target, engine, title):
    binding.safe(post(local_client, target, {'title': title}), 422, 'invalid_task_title')
    assert counts(engine) == (0, 0)


def test_unicode_title_boundary(local_client, target):
    response = post(local_client, target, {'title': ' 😀' + '😀' * 199 + ' '})
    binding.safe(response, 201)
    assert response.json()['title'] == '😀' * 200


@pytest.mark.parametrize('identifier', ['bad', 'A' * 32, 'a' * 31, 'a' * 33, 'g' * 32])
def test_invalid_path(local_client, target, engine, identifier):
    binding.safe(post(local_client, (identifier, target[1])), 422, 'invalid_task_input')
    assert counts(engine) == (0, 0)


@pytest.mark.parametrize('kind', ['missing', 'other'])
def test_inaccessible_project(local_client, target, engine, kind):
    identifier = uuid4().hex
    if kind == 'other':
        with Session(engine) as session, session.begin():
            owner = User(external_id='other-task-owner')
            session.add(Workspace(external_id=identifier, name='PRIVATE', user=owner))
    binding.safe(post(local_client, (identifier, target[1])), 404, 'workspace_not_accessible')
    assert counts(engine) == (0, 0)


@pytest.mark.parametrize('headers,code', [
    ({}, 'local_access_rejected'),
    (HEADERS | {'X-Local-Runtime-Token': 'bad'}, 'local_access_rejected'),
    (HEADERS | {'Host': 'evil.test'}, 'local_access_rejected'),
    (HEADERS | {'Origin': 'https://evil.test'}, 'local_access_rejected'),
    ({'X-Local-Runtime-Token': 'a' * 64}, 'workspace_origin_rejected'),
])
def test_boundary_before_identity(local_client, target, monkeypatch, engine, headers, code):
    def forbidden():
        pytest.fail('拒绝的请求不能读取身份')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    binding.safe(post(local_client, target, headers=headers), 403, code)
    assert counts(engine) == (0, 0)


def test_nonlocal_before_identity(local_client, target, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'account')
    def forbidden():
        pytest.fail('非本地 Task 不应解析身份')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    binding.safe(post(local_client, target), 403, 'local_mode_required')


@pytest.mark.parametrize('content_type,status,code', [
    ('text/plain', 415, 'unsupported_workspace_content_type'),
    ('application/json', 422, 'invalid_task_input'),
])
def test_malformed_body(local_client, target, engine, content_type, status, code):
    response = local_client.post(f'/workspaces/{target[0]}/tasks', headers=HEADERS | {'Content-Type': content_type}, content='{PRIVATE')
    binding.safe(response, status, code)
    assert counts(engine) == (0, 0)


@pytest.mark.parametrize('failure', [None, 'service', 'response', 'invalid-result', 'serialization'])
def test_sessions_close_and_postcommit_failure_is_uncertain(local_client, target, engine, monkeypatch, failure):
    sessions = []
    class TrackedSession(Session):
        closed = False
        def close(self):
            super().close()
            self.closed = True
    def factory():
        session = TrackedSession(engine)
        sessions.append(session)
        return session
    monkeypatch.setattr(dependencies, 'SessionLocal', factory)
    monkeypatch.setattr(workspace, 'SessionLocal', factory)
    original = workspace.create_workspace_task
    def checked(**kwargs):
        assert sessions[0].closed
        if failure == 'service':
            raise RuntimeError('PRIVATE SQL')
        result = original(**kwargs)
        if failure == 'invalid-result':
            return replace(result, external_id='PRIVATE')
        return result
    monkeypatch.setattr(workspace, 'create_workspace_task', checked)
    if failure in {'response', 'serialization'}:
        def broken(**kwargs):
            assert all(session.closed for session in sessions)
            if failure == 'serialization':
                # 让路由返回畸形普通数据，由 FastAPI 已注册的 response_model 拒绝。
                return kwargs | {'external_id': 'PRIVATE'}
            raise RuntimeError('PRIVATE response')
        monkeypatch.setattr(workspace, 'TaskResponse', broken)
    response = post(local_client, target)
    binding.safe(response, 201 if failure is None else 500, None if failure is None else 'task_creation_uncertain')
    assert len(sessions) == 2 and all(session.closed for session in sessions)
    assert counts(engine) == ((0, 0) if failure == 'service' else (1, 1))
    if failure:
        assert '未确认' in response.json()['message']
