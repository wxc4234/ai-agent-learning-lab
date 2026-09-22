"""本地目录绑定真实 HTTP、隔离数据库与安全响应测试。"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import User, Workspace
from app.routers.workspace import directories as workspace
from app.services.workspace.directory import workspace_binding
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
import tests.local.test_local_mode as local_fixtures
from tests.local.test_local_mode import HEADERS, TOKEN

local_client = local_fixtures.local_client


@pytest.fixture
def target(local_client, tmp_path):
    response = local_client.post('/workspaces', headers=HEADERS, json={'name': '项目'})
    assert response.status_code == 201
    return response.json()['external_id'], str(tmp_path.resolve())


def put(client, target, **kwargs):
    identifier, path = target
    return client.put(f'/workspaces/{identifier}/directory', headers=HEADERS, json={'root_path': path}, **kwargs)


def safe(response, status, code=None):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    assert TOKEN not in response.text
    if code:
        assert set(response.json()) == {'code', 'message'}
        assert response.json()['code'] == code
        assert 'PRIVATE' not in response.text


def stored(engine, identifier):
    with Session(engine) as session:
        return session.scalar(select(Workspace.root_path).where(Workspace.external_id == identifier))


def test_success_repeat_and_original_list_contract(local_client, target, engine):
    response = put(local_client, target)
    safe(response, 200)
    assert response.json() == {'external_id': target[0], 'name': '项目', 'root_path': target[1]}
    assert put(local_client, target).json() == response.json()
    assert stored(engine, target[0]) == target[1]
    listing = local_client.get('/workspaces', headers=HEADERS)
    safe(listing, 200)
    assert set(listing.json()['items'][0]) == {'external_id', 'name', 'created_at'}


def test_conflict_preserves_first_binding(local_client, target, tmp_path, engine):
    safe(put(local_client, target), 200)
    other = tmp_path / 'other'
    other.mkdir()
    safe(put(local_client, (target[0], str(other))), 409, 'workspace_already_bound')
    assert stored(engine, target[0]) == target[1]


@pytest.mark.parametrize('payload', [{}, {'root_path': ''}, {'root_path': None}, {'root_path': 1}, {'root_path': []}, {'root_path': 'PRIVATE', 'user_id': 1}])
def test_strict_body(local_client, target, payload, engine):
    response = local_client.put(f'/workspaces/{target[0]}/directory', headers=HEADERS, json=payload)
    safe(response, 422, 'invalid_workspace_input')
    assert stored(engine, target[0]) is None


@pytest.mark.parametrize('identifier', ['short', 'A' * 32, 'a' * 31, 'a' * 33, 'g' * 32])
def test_identifier(local_client, target, identifier):
    safe(put(local_client, (identifier, target[1])), 422, 'invalid_workspace_input')


@pytest.mark.parametrize('kind', ['relative', 'missing', 'file', 'root'])
def test_real_directory_failures(local_client, target, tmp_path, kind, engine):
    file = tmp_path / 'file'
    file.write_text('content')
    path, code = {
        'relative': ('relative', 'invalid_directory_path'),
        'missing': (str(tmp_path / 'missing'), 'directory_not_found'),
        'file': (str(file), 'not_a_directory'),
        'root': (tmp_path.anchor, 'root_directory_not_allowed'),
    }[kind]
    safe(put(local_client, (target[0], path)), 422, code)
    assert stored(engine, target[0]) is None


@pytest.mark.parametrize('kind', ['missing', 'other_owner'])
def test_inaccessible_resource_never_checks_directory(local_client, target, engine, monkeypatch, kind):
    identifier = uuid4().hex
    if kind == 'other_owner':
        with Session(engine) as session, session.begin():
            owner = User(external_id='other-resource-owner')
            session.add(owner)
            session.flush()
            session.add(Workspace(external_id=identifier, user_id=owner.id, name='其他项目'))
    def forbidden(path):
        pytest.fail('不可访问资源不应访问文件系统')
    monkeypatch.setattr(workspace_binding, 'validate_workspace_directory', forbidden)
    safe(put(local_client, (identifier, target[1])), 404, 'workspace_not_accessible')


@pytest.mark.parametrize('headers,code', [
    ({}, 'local_access_rejected'),
    (HEADERS | {'X-Local-Runtime-Token': 'b' * 64}, 'local_access_rejected'),
    (HEADERS | {'Origin': 'https://evil.test'}, 'local_access_rejected'),
    (HEADERS | {'Host': 'evil.test'}, 'local_access_rejected'),
    ({'X-Local-Runtime-Token': TOKEN}, 'workspace_origin_rejected'),
])
def test_request_boundary(local_client, target, headers, code, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail('请求边界未提前拒绝')
    monkeypatch.setattr(workspace, 'bind_workspace_directory', forbidden)
    response = local_client.put(f'/workspaces/{target[0]}/directory', headers=headers, json={'root_path': target[1]})
    safe(response, 403, code)


def test_nonlocal_mode_rejected_before_identity(local_client, target, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'account')
    def forbidden():
        pytest.fail('非本地绑定请求不应解析身份')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    safe(put(local_client, target), 403, 'local_mode_required')


@pytest.mark.parametrize('content_type,status,code', [
    ('text/plain', 415, 'unsupported_workspace_content_type'),
    ('application/json', 422, 'invalid_workspace_input'),
])
def test_content_type_and_malformed_json(local_client, target, content_type, status, code):
    response = local_client.put(f'/workspaces/{target[0]}/directory', headers=HEADERS | {'Content-Type': content_type}, content='{PRIVATE')
    safe(response, status, code)


@pytest.mark.parametrize('code,status', [('directory_access_denied', 422), ('directory_unavailable', 503), ('unknown', 500)])
def test_directory_error_mapping(local_client, target, monkeypatch, code, status):
    def fail(path):
        raise WorkspaceDirectoryError(code, 'PRIVATE details')
    monkeypatch.setattr(workspace_binding, 'validate_workspace_directory', fail)
    safe(put(local_client, target), status, code if code != 'unknown' else 'workspace_binding_failed')


@pytest.mark.parametrize('failure', ['service', 'response'])
def test_sessions_close_and_failures_are_safe(local_client, target, engine, monkeypatch, failure):
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
    original = workspace.bind_workspace_directory
    def checked(**kwargs):
        assert sessions[0].closed
        if failure == 'service':
            raise RuntimeError('PRIVATE SQL')
        return original(**kwargs)
    monkeypatch.setattr(workspace, 'bind_workspace_directory', checked)
    if failure == 'response':
        def broken_response(**kwargs):
            assert all(s.closed for s in sessions)
            raise RuntimeError('PRIVATE response')
        monkeypatch.setattr(workspace, 'WorkspaceDirectoryResponse', broken_response)
    safe(put(local_client, target), 500, 'workspace_binding_failed')
    assert len(sessions) == 2 and all(s.closed for s in sessions)
    # 提交后响应失败时记录可能已保存，不能告诉用户一定没有绑定。
    assert stored(engine, target[0]) == (target[1] if failure == 'response' else None)
