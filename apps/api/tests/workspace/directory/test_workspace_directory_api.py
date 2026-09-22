"""目录状态回读：本地真实 HTTP 和隔离 PostgreSQL。"""

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import User, Workspace
from app.routers.workspace import directories as workspace
import tests.workspace.directory.test_workspace_binding_api as binding
from tests.local.test_local_mode import HEADERS, TOKEN

local_client = binding.local_client
target = binding.target


def get(client, target, headers=HEADERS):
    return client.get(f'/workspaces/{target[0]}/directory', headers=headers)


def test_unbound_bound_and_removed_directory_readback(local_client, target, tmp_path):
    response = get(local_client, target, {'X-Local-Runtime-Token': TOKEN})
    binding.safe(response, 200)
    assert response.json() == {'external_id': target[0], 'name': '项目', 'root_path': None}
    directory = tmp_path / '真实 项目'
    directory.mkdir()
    binding.safe(binding.put(local_client, (target[0], str(directory))), 200)
    directory.rmdir()
    # 读取保存的状态，不因目录现在不存在而回报未绑定。
    response = get(local_client, target)
    binding.safe(response, 200)
    assert response.json()['root_path'] == str(directory.resolve())


@pytest.mark.parametrize('identifier', ['x', 'A' * 32, 'a' * 31, 'a' * 33, 'g' * 32])
def test_invalid_id(local_client, target, identifier):
    binding.safe(get(local_client, (identifier, target[1])), 422, 'invalid_workspace_input')


@pytest.mark.parametrize('kind', ['missing', 'other_owner'])
def test_resource_boundary(local_client, target, engine, kind):
    identifier = uuid4().hex
    if kind == 'other_owner':
        with Session(engine) as session, session.begin():
            owner = User(external_id='other-directory-owner')
            session.add(owner)
            session.flush()
            session.add(Workspace(external_id=identifier, user_id=owner.id, name='PRIVATE', root_path='/PRIVATE'))
    binding.safe(get(local_client, (identifier, target[1])), 404, 'workspace_not_accessible')


@pytest.mark.parametrize('headers', [{}, HEADERS | {'X-Local-Runtime-Token': 'b' * 64}, HEADERS | {'Host': 'evil.test'}, HEADERS | {'Origin': 'https://evil.test'}])
def test_local_boundary_before_query(local_client, target, monkeypatch, headers):
    def forbidden(**kwargs):
        pytest.fail('拒绝的请求不能查询目录')
    monkeypatch.setattr(workspace, 'require_owned_workspace', forbidden)
    binding.safe(get(local_client, target, headers), 403, 'local_access_rejected')


def test_nonlocal_rejected_before_identity(local_client, target, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'account')
    def forbidden():
        pytest.fail('非本地目录读取不能进入身份查询')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    binding.safe(get(local_client, target), 403, 'local_mode_required')


@pytest.mark.parametrize('failure', [None, 'query', 'response'])
def test_readonly_and_sessions_closed(local_client, target, engine, monkeypatch, failure):
    auth_sessions = []
    business_sessions = []
    class TrackedSession(Session):
        closed = False
        def close(self):
            super().close()
            self.closed = True
    class ReadSession(TrackedSession):
        def commit(self):
            pytest.fail('目录读取不能提交')
    def auth_factory():
        session = TrackedSession(engine)
        auth_sessions.append(session)
        return session
    def read_factory():
        session = ReadSession(engine)
        business_sessions.append(session)
        return session
    monkeypatch.setattr(dependencies, 'SessionLocal', auth_factory)
    monkeypatch.setattr(workspace, 'SessionLocal', read_factory)
    original = workspace.require_owned_workspace
    def checked(**kwargs):
        assert auth_sessions[-1].closed
        if failure == 'query':
            raise RuntimeError('PRIVATE SQL')
        result = original(**kwargs)
        if failure == 'response':
            # 不修改 ORM 或数据库，仅制造无效响应类型。
            from types import SimpleNamespace
            return SimpleNamespace(external_id=None, name='PRIVATE', root_path=None)
        return result
    monkeypatch.setattr(workspace, 'require_owned_workspace', checked)
    response = get(local_client, target)
    binding.safe(response, 500 if failure else 200, 'workspace_directory_read_failed' if failure else None)
    assert len(auth_sessions) == len(business_sessions) == 1
    assert all(session.closed for session in auth_sessions + business_sessions)
    assert binding.stored(engine, target[0]) is None


def test_read_does_not_lock_or_wait_for_binding_writer(local_client, target, engine, monkeypatch):
    def factory():
        session = Session(engine)
        session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        return session
    monkeypatch.setattr(workspace, 'SessionLocal', factory)
    with Session(engine) as writer:
        row = writer.scalar(select(Workspace).where(Workspace.external_id == target[0]).with_for_update())
        row.root_path = '/not-committed'
        writer.flush()
        # 普通 MVCC 读取返回已提交的 NULL，不等待写锁或泄露未提交值。
        response = get(local_client, target)
        binding.safe(response, 200)
        assert response.json()['root_path'] is None
        writer.rollback()
