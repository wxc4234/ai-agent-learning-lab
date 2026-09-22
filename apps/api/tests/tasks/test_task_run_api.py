"""运行列表 HTTP：真实 PostgreSQL 分页、授权和安全错误边界。"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import Conversation, User, Workspace
from app.routers.workspace import tasks as workspace
from app.services.tasks import task_run_query as service
from tests.local.test_local_mode import HEADERS, TOKEN
from tests.tasks.test_task_workspace import task
from tests.tasks.test_task_run_query import seed
import tests.workspace.directory.test_workspace_binding_api as binding

local_client = binding.local_client
target = binding.target


@pytest.fixture(autouse=True)
def query_database(engine, monkeypatch):
    # 查询连接限定在根夹具隔离库；业务查询不得提交，异常也必须释放连接。
    sessions = []

    class ReadSession(Session):
        closed = False

        def commit(self):
            pytest.fail('query must not commit')

        def close(self):
            super().close()
            self.closed = True

    def factory():
        session = ReadSession(engine)
        sessions.append(session)
        return session

    monkeypatch.setattr(service, 'SessionLocal', factory)
    yield
    assert all(session.closed and not session.in_transaction() for session in sessions)


def populate(engine, created, count):
    with Session(engine) as session:
        pk = session.scalar(select(Conversation.id).where(Conversation.external_id == created['conversation_id']))
    return seed(engine, {'conversation_pk': pk}, count)


@pytest.mark.parametrize('count,limit', [(0, 2), (2, 2), (5, 2), (51, 50)])
def test_pagination_payload_and_isolation(local_client, target, engine, count, limit):
    created, path = task(local_client, target)
    sibling, _ = task(local_client, target)
    ids = populate(engine, created, count)
    populate(engine, sibling, 1)
    params = {'limit': limit}
    seen = []
    while True:
        response = local_client.get(path + '/runs', headers=HEADERS, params=params)
        binding.safe(response, 200)
        data = response.json()
        assert set(data) == {'workspace_id', 'task_id', 'items', 'next_cursor'}
        assert data['workspace_id'] == target[0] and data['task_id'] == created['external_id']
        for item in data['items']:
            assert set(item) == {'run_id', 'status', 'started_at', 'finished_at', 'duration_ms'}
            assert item['status'] == 'done' and item['duration_ms'] == 1234
            assert isinstance(item['started_at'], str) and isinstance(item['finished_at'], str)
            seen.append(item['run_id'])
        if data['next_cursor'] is None:
            break
        assert data['next_cursor'] == str(data['items'][-1]['run_id'])
        params['before'] = data['next_cursor']
    assert seen == ids[::-1]


def test_default_limit_and_no_origin(local_client, target, engine):
    created, path = task(local_client, target)
    ids = populate(engine, created, 21)
    response = local_client.get(path + '/runs', headers={
        'X-Local-Runtime-Token': TOKEN, 'Cookie': 'agent_session=forged', 'X-User-ID': '999',
    }, params={'user_id': 999})
    binding.safe(response, 200)
    assert [item['run_id'] for item in response.json()['items']] == ids[:0:-1]
    assert response.json()['next_cursor'] == str(ids[1])


@pytest.mark.parametrize('field,value', [
    ('before', '0'), ('before', '-1'), ('before', '2147483648'),
    ('before', 'PRIVATE'), ('before', ''), ('before', 'true'), ('before', '1.5'),
    ('limit', '0'), ('limit', '-1'), ('limit', '51'),
    ('limit', 'PRIVATE'), ('limit', ''), ('limit', 'true'), ('limit', '1.5'),
])
def test_invalid_query(local_client, target, monkeypatch, field, value):
    _, path = task(local_client, target)

    def forbidden(**kwargs):
        pytest.fail('invalid query reached service')

    monkeypatch.setattr(workspace, 'list_task_runs', forbidden)
    binding.safe(local_client.get(path + '/runs', headers=HEADERS, params={field: value}),
                 422, 'invalid_task_run_query')


@pytest.mark.parametrize('field', ['workspace', 'task'])
@pytest.mark.parametrize('identifier', ['bad', 'A' * 32, 'a' * 33, 'g' * 32])
def test_invalid_path(local_client, target, monkeypatch, field, identifier):
    created, path = task(local_client, target)
    path = path.replace(target[0] if field == 'workspace' else created['external_id'], identifier)

    def forbidden(**kwargs):
        pytest.fail('invalid path reached service')

    monkeypatch.setattr(workspace, 'list_task_runs', forbidden)
    binding.safe(local_client.get(path + '/runs', headers=HEADERS), 422, 'invalid_task_run_query')


@pytest.mark.parametrize('kind', ['missing-task', 'missing-workspace', 'wrong-project', 'foreign-project', 'foreign-conversation'])
def test_inaccessible_empty_task(local_client, target, engine, kind):
    created, path = task(local_client, target)
    if kind == 'missing-task':
        path = path.replace(created['external_id'], 'f' * 32)
    elif kind == 'missing-workspace':
        path = path.replace(target[0], 'f' * 32)
    elif kind == 'wrong-project':
        response = local_client.post('/workspaces', headers=HEADERS, json={'name': '其他项目'})
        assert response.status_code == 201
        path = path.replace(target[0], response.json()['external_id'])
    else:
        with Session(engine) as session, session.begin():
            owner = User(external_id='foreign-owner')
            session.add(owner)
            session.flush()
            model, identifier = (Workspace, target[0]) if kind == 'foreign-project' else (Conversation, created['conversation_id'])
            session.scalar(select(model).where(model.external_id == identifier)).user_id = owner.id
    binding.safe(local_client.get(path + '/runs', headers=HEADERS), 404, 'workspace_not_accessible')


@pytest.mark.parametrize('headers', [
    {}, HEADERS | {'X-Local-Runtime-Token': 'bad'},
    HEADERS | {'Host': 'evil.test'}, HEADERS | {'Origin': 'http://localhost:3000.evil.test'},
])
def test_boundary_before_identity(local_client, target, monkeypatch, headers):
    _, path = task(local_client, target)

    def forbidden():
        pytest.fail('boundary must precede identity')

    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    binding.safe(local_client.get(path + '/runs', headers=headers), 403, 'local_access_rejected')


def test_local_only(local_client, target, monkeypatch):
    _, path = task(local_client, target)
    monkeypatch.setattr(settings, 'app_mode', 'account')

    def forbidden():
        pytest.fail('mode rejection must precede identity')

    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    binding.safe(local_client.get(path + '/runs', headers=HEADERS), 403, 'local_mode_required')


@pytest.mark.parametrize('failure', ['domain', 'internal', 'database', 'response'])
def test_safe_failures(local_client, target, monkeypatch, caplog, failure):
    _, path = task(local_client, target)

    def broken(**kwargs):
        if failure == 'domain':
            raise service.InvalidTaskRunQueryError()
        if failure == 'database':
            # 真实 SQL 失败与响应结构错误均是服务器错误，不能误报参数 422。
            with service.SessionLocal() as session:
                session.execute(text('SELECT 1 / 0 AS "PRIVATE"'))
        if failure == 'response':
            return {'items': 'PRIVATE'}
        raise RuntimeError('PRIVATE SQL /path/token')

    monkeypatch.setattr(workspace, 'list_task_runs', broken)
    response = local_client.get(path + '/runs', headers=HEADERS)
    binding.safe(response, 422 if failure == 'domain' else 500,
                 'invalid_task_run_query' if failure == 'domain' else 'task_run_list_failed')
    assert 'PRIVATE' not in response.text + caplog.text


def test_openapi(local_client):
    operation = local_client.get('/openapi.json', headers=HEADERS).json()['paths'][
        '/workspaces/{workspace_id}/tasks/{task_id}/runs'
    ]['get']
    assert 'requestBody' not in operation
    assert {'200', '403', '404', '422', '500'} <= operation['responses'].keys()
    parameters = {item['name']: item['schema'] for item in operation['parameters']}
    assert parameters['limit']['maximum'] == 50
    assert parameters['limit']['minimum'] == 1
    assert parameters['limit']['default'] == 20
    assert operation['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/TaskRunListResponse')
