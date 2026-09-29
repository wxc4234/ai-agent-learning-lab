"""真实身份边界、已组装路由及隔离PG/临时Git数据；不运行完整应用生命周期。"""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import User, Workspace
from app.local_boundary import local_access_boundary
from app.routers.workspace import git_staged as route
from app.routers.workspace.router import router
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.workspace.git.project_source import ProjectGitSourceError
from tests.assertions import require_value
from tests.workspace.git.test_project_staged import files, ready, repository, setup, target
from tests.workspace.git.test_index_v2 import fixture as index_bytes, entry

__all__ = ['files', 'ready', 'repository', 'setup', 'target']
TOKEN = 'a' * 64
HEADERS = {'X-Local-Runtime-Token': TOKEN, 'Origin': 'http://localhost:3000'}


@pytest.fixture
def api(setup, files, target, engine, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(settings, 'local_runtime_token', SecretStr(TOKEN))
    monkeypatch.setattr(dependencies, 'SessionLocal', lambda: Session(engine))
    monkeypatch.setattr(route, 'SessionLocal', lambda: Session(engine))
    # 预置真实本机身份，观察业务表；身份依赖仍执行既有幂等初始化语句。
    with Session(engine) as session, session.begin():
        user = require_value(session.get(User, target['user_id']))
        user.external_id = LOCAL_USER_ID
        user.username = user.password_hash = None
    app = FastAPI()
    app.middleware("http")(local_access_boundary)
    app.include_router(router)
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        yield client, f"/workspaces/{target['workspace_id']}/tasks/{target['task_id']}/git/staged"


def get(api, query='binding_revision=1', headers=None):
    return api[0].get(api[1] + '?' + query, headers=HEADERS if headers is None else headers)


@pytest.mark.parametrize('kind', ['equal', 'modified', 'conflict', 'missing'])
def test_real_api_safe_projection_no_business_mutation(api, files, setup, engine, kind):
    if kind == 'modified':
        files[3].write_bytes(index_bytes(entry(b'dir/file', mode=0o100755)))
    elif kind == 'conflict':
        files[3].write_bytes(index_bytes(entry(b'dir/file', stage=2)))
    elif kind == 'missing':
        files[3].unlink()
    statements = []
    def record(conn, cursor, sql, parameters, context, executemany):
        statements.append(sql.lower())
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files if p.exists()]
    event.listen(engine, 'before_cursor_execute', record)
    try:
        response = get(api)
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert set(data) == {'workspace_id', 'task_id', 'binding_revision', 'scope', 'status', 'unavailable_reasons', 'changes'}
    assert data['scope'] == 'staged_only'
    assert data['changes'] == (None if kind == 'missing' else [] if kind == 'equal' else data['changes'])
    if kind in ('modified', 'conflict'):
        change, = data['changes']
        assert change['status'] == ('modified' if kind == 'modified' else 'unmerged')
        assert set(change) == {'path', 'status', 'head', 'index'}
        assert set(change['head']) == {'mode', 'object_id'}
        assert set(change['index'][0]) == {'stage', 'version'}
    if kind == 'missing':
        assert data['status'] == 'comparison_unavailable' and data['unavailable_reasons'] == ['index_missing']
    assert str(setup[1]) not in response.text
    assert all(key not in response.text for key in ['bound_root', 'workspace_pk', 'cached_stat', 'config_sha256', TOKEN])
    # 唯一非SELECT是已有本机身份的INSERT ON CONFLICT，不发生业务写入。
    assert all(s.startswith('select') or ('insert into' in s and 'users' in s and 'on conflict' in s) for s in statements)
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in files if p.exists()]


@pytest.mark.parametrize('query', ['', 'binding_revision=0', 'binding_revision=-1', 'binding_revision=01',
    'binding_revision=1.0', 'binding_revision=9007199254740992', 'binding_revision=1&binding_revision=1',
    'binding_revision=1&user_id=123', 'binding_revision=1&path=/private', 'binding_revision=true'])
def test_bad_query_rejected_before_observation(api, monkeypatch, query):
    monkeypatch.setattr(route, 'read_project_staged', lambda *a: pytest.fail('invalid request'))
    response = get(api, query)
    assert response.status_code == 422 and response.json()['code'] == 'invalid_staged_input'
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('kind', ['foreign', 'missing-task', 'stale'])
def test_real_ownership_and_revision(api, engine, target, kind):
    if kind == 'foreign':
        with Session(engine) as session, session.begin():
            require_value(session.scalar(select(Workspace))).user_id = target['other_id']
    if kind == 'missing-task':
        response = api[0].get(api[1].replace(target['task_id'], 'f' * 32) + '?binding_revision=1', headers=HEADERS)
    else:
        response = get(api, 'binding_revision=2' if kind == 'stale' else 'binding_revision=1')
    assert response.status_code == (409 if kind == 'stale' else 404)
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('headers', [{}, HEADERS | {'X-Local-Runtime-Token': 'b' * 64},
                                    HEADERS | {'Host': 'evil.test'}, HEADERS | {'Origin': 'https://evil.test'}])
def test_existing_local_boundary(api, monkeypatch, headers):
    monkeypatch.setattr(route, 'read_project_staged', lambda *a: pytest.fail('boundary first'))
    assert get(api, headers=headers).status_code == 403


@pytest.mark.parametrize('code,status,public', [
    ('project_git_source_changed', 409, 'staged_observation_changed'),
    ('project_git_tree_graph_limit', 422, 'staged_observation_limit'),
    ('index_v2_extensions_unsupported', 422, 'staged_observation_unsupported'),
    ('project_git_source_unavailable', 500, 'staged_read_failed'),
    ('unknown/private/path', 500, 'staged_read_failed'),
])
def test_error_mapping_does_not_reflect_private_errors(api, monkeypatch, code, status, public):
    def failed(*args):
        raise ProjectGitSourceError(code)
    monkeypatch.setattr(route, 'read_project_staged', failed)
    response = get(api)
    assert response.status_code == status and response.json()['code'] == public
    assert 'private' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_real_corrupt_index_is_not_empty(api, files):
    files[3].write_bytes(b'bad')
    response = get(api)
    assert response.status_code == 422 and response.json()['code'] == 'staged_observation_unsupported'


def test_final_utf8_response_byte_budget(api, files, monkeypatch):
    files[3].write_bytes(index_bytes(entry('中文'.encode())))
    response = get(api)
    assert response.status_code == 200
    monkeypatch.setattr(route, 'MAX_RESPONSE_BYTES', len(response.content))
    assert get(api).content == response.content
    monkeypatch.setattr(route, 'MAX_RESPONSE_BYTES', len(response.content) - 1)
    exceeded = get(api)
    assert exceeded.status_code == 422 and exceeded.json()['code'] == 'staged_response_limit'
    assert 'changes' not in exceeded.json()


def test_inconsistent_service_result_fails_closed(api, monkeypatch):
    original = route.read_project_staged
    def bad(request):
        return replace(original(request), status='comparison_unavailable')
    monkeypatch.setattr(route, 'read_project_staged', bad)
    response = get(api)
    assert response.status_code == 500 and response.json()['code'] == 'staged_read_failed'


@pytest.mark.parametrize('kind', ['bound', 'unbound', 'foreign', 'query'])
def test_binding_metadata(api, engine, target, kind):
    with Session(engine) as session, session.begin():
        workspace = require_value(session.scalar(select(Workspace)))
        if kind == 'unbound':
            workspace.root_path = None
        elif kind == 'foreign':
            workspace.user_id = target['other_id']
    url = api[1].removesuffix('staged') + 'binding' + ('?path=/private' if kind == 'query' else '')
    response = api[0].get(url, headers=HEADERS)
    assert response.status_code == {'foreign': 404, 'query': 422}.get(kind, 200)
    if response.status_code == 200:
        assert response.json() == {'workspace_id': target['workspace_id'], 'task_id': target['task_id'],
                                   'binding_revision': 1, 'bound': kind == 'bound'}
