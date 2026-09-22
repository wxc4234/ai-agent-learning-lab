"""任务详情：真实 HTTP、隔离 PostgreSQL、归属与只读边界。"""

from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Task, User, Workspace
from app.routers.workspace import tasks as workspace
from app.services.tasks import task_workspace
from tests.local.test_local_mode import HEADERS
import tests.workspace.directory.test_workspace_binding_api as binding
from tests.tasks.test_task_workspace import task

local_client = binding.local_client
target = binding.target


def test_public_detail_and_forged_identity_are_safe(local_client, target):
    created, path = task(local_client, target)
    response = local_client.get(path, headers=HEADERS | {'Cookie': 'agent_session=forged', 'X-User-ID': '999'})
    binding.safe(response, 200)
    data = response.json()
    assert set(data) == {'workspace', 'task'}
    assert set(data['workspace']) == {'external_id', 'name', 'created_at'}
    assert data['workspace']['external_id'] == target[0]
    assert data['workspace']['name'] == '项目'
    assert data['task'] == created


def test_old_task_is_readable_outside_first_page(local_client, target):
    created, path = task(local_client, target)
    for _ in range(21):
        task(local_client, target)
    listing = local_client.get(f'/workspaces/{target[0]}/tasks', headers=HEADERS).json()
    assert len(listing['items']) == 20
    assert created['external_id'] not in {row['external_id'] for row in listing['items']}
    response = local_client.get(path, headers=HEADERS)
    binding.safe(response, 200)
    assert response.json()['task'] == created


@pytest.mark.parametrize('kind', ['unknown-task', 'unknown-project', 'wrong-project', 'foreign-project', 'foreign-conversation'])
def test_inaccessible_resources_share_404(local_client, target, engine, kind):
    created, path = task(local_client, target)
    if kind == 'unknown-task':
        path = path.rsplit('/', 1)[0] + '/' + uuid4().hex
    elif kind == 'unknown-project':
        path = path.replace(target[0], uuid4().hex)
    elif kind == 'wrong-project':
        other = local_client.post('/workspaces', headers=HEADERS, json={'name': '另一项目'}).json()
        path = path.replace(target[0], other['external_id'])
    else:
        with Session(engine) as session, session.begin():
            owner = User(external_id=uuid4().hex)
            session.add(owner)
            session.flush()
            if kind == 'foreign-project':
                row = session.scalar(select(Workspace).where(Workspace.external_id == target[0]))
            else:
                row = session.scalar(select(Conversation).where(Conversation.external_id == created['conversation_id']))
            row.user_id = owner.id
    response = local_client.get(path, headers=HEADERS)
    binding.safe(response, 404, 'workspace_not_accessible')


@pytest.mark.parametrize('field', ['workspace', 'task'])
@pytest.mark.parametrize('value', ['bad', 'A' * 32, 'a' * 31, 'g' * 32])
def test_invalid_identifiers(local_client, target, field, value):
    created, path = task(local_client, target)
    path = path.replace(target[0] if field == 'workspace' else created['external_id'], value)
    binding.safe(local_client.get(path, headers=HEADERS), 422)


def test_nonlocal_rejected_before_service(local_client, target, monkeypatch):
    _, path = task(local_client, target)
    monkeypatch.setattr(settings, 'app_mode', 'account')
    def forbidden(**kwargs):
        pytest.fail('service must not execute in account mode')
    monkeypatch.setattr(workspace, 'task_detail', forbidden)
    binding.safe(local_client.get(path, headers=HEADERS), 403, 'local_mode_required')


def test_service_is_read_only_and_result_survives_close(local_client, target, engine, monkeypatch):
    created, _ = task(local_client, target)
    with Session(engine) as session:
        user_id = session.scalar(select(Workspace.user_id).where(Workspace.external_id == target[0]))
    sessions = []
    statements = []
    class ReadSession(Session):
        closed_by_service = False
        def commit(self):
            pytest.fail('read service must not commit')
        def close(self):
            self.closed_by_service = True
            super().close()
    def factory():
        session = ReadSession(engine)
        sessions.append(session)
        return session
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    monkeypatch.setattr(task_workspace, 'SessionLocal', factory)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        result = task_workspace.task_detail(user_id, target[0], created['external_id'])
        assert len(sessions) == 1 and sessions[0].closed_by_service
        before_serialization = len(statements)
        assert result.model_dump(mode='json')['task'] == created
        assert len(statements) == before_serialization
        assert statements and all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    with Session(engine) as session:
        assert session.scalar(select(Task.title).where(Task.external_id == created['external_id'])) == created['title']


def test_backend_failure_is_sanitized(local_client, target, monkeypatch):
    _, path = task(local_client, target)
    def failed(**kwargs):
        raise RuntimeError('PRIVATE database details')
    monkeypatch.setattr(workspace, 'task_detail', failed)
    response = local_client.get(path, headers=HEADERS)
    binding.safe(response, 500)
    assert 'PRIVATE' not in response.text
