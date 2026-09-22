"""真实 PostgreSQL 工作台读取/归属/首轮总结失败与持久化。"""
import pytest
from sqlalchemy.orm import Session
from app.models import Conversation, Message
from sqlalchemy import select
from app.services.tasks import task_workspace
import tests.workspace.directory.test_workspace_binding_api as binding
from tests.local.test_local_mode import HEADERS
from tests.tasks.test_task_api import post

local_client = binding.local_client
target = binding.target


def task(client, target):
    result = post(client, target, {'title': '请实现搜索'}).json()
    return result, f"/workspaces/{target[0]}/tasks/{result['external_id']}"


def test_list_pages_and_messages(local_client, target):
    first, path = task(local_client, target)
    second, _ = task(local_client, target)
    endpoint = f'/workspaces/{target[0]}/tasks'
    data = local_client.get(endpoint + '?limit=1', headers=HEADERS).json()
    assert data['items'][0]['external_id'] == second['external_id']
    following = local_client.get(endpoint + '?limit=1&before=' + data['next_cursor'], headers=HEADERS).json()
    assert following['items'][0]['external_id'] == first['external_id']
    assert following['next_cursor'] is None
    assert local_client.get(path + '/messages', headers=HEADERS).json() == {'messages': []}


def persist(engine, task):
    with Session(engine) as session, session.begin():
        conversation = session.scalar(select(Conversation).where(Conversation.external_id == task['conversation_id']))
        session.add_all([Message(conversation_id=conversation.id, role=role, content=content) for role, content in [('user', '请实现搜索'), ('assistant', '可以先增加搜索接口')]])


def test_summary_persisted_and_not_repeated(local_client, target, engine, monkeypatch):
    created, path = task(local_client, target)
    persist(engine, created)
    calls = []
    async def generate(messages):
        calls.append(messages)
        return '实现搜索功能'
    monkeypatch.setattr(task_workspace, 'generate_title', generate)
    for _ in range(2):
        response = local_client.post(path + '/title', headers=HEADERS, json={})
        assert response.status_code == 200
        assert response.json()['title'] == '实现搜索功能'
    assert len(calls) == 1
    messages = local_client.get(path + '/messages', headers=HEADERS).json()['messages']
    assert messages == [{'role': 'user', 'content': '请实现搜索'}, {'role': 'assistant', 'content': '可以先增加搜索接口'}]


@pytest.mark.parametrize('output', ['', '\ninvalid\ntitle', RuntimeError('PRIVATE')])
def test_title_failure_keeps_excerpt(local_client, target, engine, monkeypatch, output):
    created, path = task(local_client, target)
    persist(engine, created)
    async def generate(messages):
        if isinstance(output, Exception):
            raise output
        return output
    monkeypatch.setattr(task_workspace, 'generate_title', generate)
    assert local_client.post(path + '/title', headers=HEADERS, json={}).json() == {'title': '请实现搜索'}


def test_empty_task_never_calls_model(local_client, target, monkeypatch):
    _, path = task(local_client, target)
    async def forbidden(messages):
        pytest.fail('must not generate without a complete first turn')
    monkeypatch.setattr(task_workspace, 'generate_title', forbidden)
    assert local_client.post(path + '/title', headers=HEADERS, json={}).json() == {'title': '请实现搜索'}


@pytest.mark.parametrize('suffix', ['/messages', '/title'])
def test_wrong_workspace_or_unknown_task_rejected(local_client, target, monkeypatch, suffix):
    _, path = task(local_client, target)
    async def forbidden(messages):
        pytest.fail('authorization before model')
    monkeypatch.setattr(task_workspace, 'generate_title', forbidden)
    for wrong in [path.replace(target[0], 'f' * 32), path.rsplit('/', 1)[0] + '/' + 'e' * 32]:
        response = local_client.get(wrong + suffix, headers=HEADERS) if suffix == '/messages' else local_client.post(wrong + suffix, headers=HEADERS, json={})
        assert response.status_code == 404


def test_title_write_requires_origin_json_and_empty_body(local_client, target):
    _, path = task(local_client, target)
    assert local_client.post(path + '/title', headers={k: v for k, v in HEADERS.items() if k.lower() != 'origin'}, json={}).status_code == 403
    assert local_client.post(path + '/title', headers=HEADERS, json={'title': 'injected'}).status_code == 422


def test_late_summary_does_not_overwrite_changed_title(local_client, target, engine, monkeypatch):
    from app.models import Task
    created, path = task(local_client, target)
    persist(engine, created)
    async def generate(messages):
        with Session(engine) as session, session.begin():
            row = session.scalar(select(Task).where(Task.external_id == created['external_id']))
            row.title = '用户后来的标题'
        return '迟到的标题'
    monkeypatch.setattr(task_workspace, 'generate_title', generate)
    assert local_client.post(path + '/title', headers=HEADERS, json={}).json() == {'title': '用户后来的标题'}


def test_other_owner_cannot_list_read_or_summarize(local_client, target, engine, monkeypatch):
    from app.models import User, Workspace
    _, path = task(local_client, target)
    with Session(engine) as session, session.begin():
        owner = User(external_id='different-task-owner')
        session.add(owner)
        session.flush()
        workspace = session.scalar(select(Workspace).where(Workspace.external_id == target[0]))
        workspace.user_id = owner.id
    async def forbidden(messages):
        pytest.fail('must authorize before model')
    monkeypatch.setattr(task_workspace, 'generate_title', forbidden)
    assert local_client.get(f'/workspaces/{target[0]}/tasks', headers=HEADERS).status_code == 404
    assert local_client.get(path + '/messages', headers=HEADERS).status_code == 404
    assert local_client.post(path + '/title', headers=HEADERS, json={}).status_code == 404
