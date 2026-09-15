"""真实 HTTP/隔离 PostgreSQL，只有系统目录选择器使用替身。"""

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.routers.workspace import workspace
from app.services.workspace.directory_picker import DirectoryPickerError
import tests.workspace.test_workspace_binding_api as binding
from tests.local.test_local_mode import HEADERS

local_client = binding.local_client
target = binding.target


def post(client, target, headers=None, body=None):
    return client.post(f'/workspaces/{target[0]}/directory/select', headers=HEADERS if headers is None else headers, json={} if body is None else body)


def test_selection_saved_cancelled_and_already_bound(local_client, target, monkeypatch):
    calls = []
    def choose():
        calls.append(1)
        return None if len(calls) == 1 else target[1]
    monkeypatch.setattr(workspace, 'select_directory', choose)
    binding.safe(post(local_client, target), 204)
    assert local_client.get(f'/workspaces/{target[0]}/directory', headers=HEADERS).json()['root_path'] is None
    response = post(local_client, target)
    binding.safe(response, 200)
    assert response.json()['root_path'] == target[1]
    binding.safe(post(local_client, target), 200)
    assert len(calls) == 2


@pytest.mark.parametrize('kind', ['missing', 'origin', 'token', 'body', 'mode'])
def test_denied_before_dialog(local_client, target, monkeypatch, kind):
    def forbidden():
        pytest.fail('拒绝的请求不能打开系统窗口')
    monkeypatch.setattr(workspace, 'select_directory', forbidden)
    if kind == 'missing':
        response = post(local_client, ('f' * 32, target[1]))
        expected = 404
    elif kind == 'mode':
        monkeypatch.setattr(settings, 'app_mode', 'account')
        response = post(local_client, target)
        expected = 403
    elif kind == 'body':
        response = post(local_client, target, body={'path': '/PRIVATE'})
        expected = 422
    else:
        headers = HEADERS | ({'Origin': 'https://evil.test'} if kind == 'origin' else {'X-Local-Runtime-Token': 'bad'})
        response = post(local_client, target, headers=headers)
        expected = 403
    binding.safe(response, expected)


@pytest.mark.parametrize('code,status', [('directory_picker_busy', 409), ('directory_picker_timeout', 408), ('directory_picker_unsupported', 501), ('directory_picker_unavailable', 503)])
def test_picker_error(local_client, target, monkeypatch, code, status):
    def choose():
        raise DirectoryPickerError(code)
    monkeypatch.setattr(workspace, 'select_directory', choose)
    binding.safe(post(local_client, target), status, code)


def test_no_open_session_during_dialog_and_concurrent_binding(local_client, target, monkeypatch, engine, tmp_path):
    sessions = []
    class Tracked(Session):
        closed = False
        def close(self):
            super().close()
            self.closed = True
    def factory():
        session = Tracked(engine)
        sessions.append(session)
        return session
    monkeypatch.setattr(workspace, 'SessionLocal', factory)
    other = tmp_path / 'other'
    other.mkdir()
    def choose():
        assert all(session.closed for session in sessions)
        # 用户选择期间另一请求已绑定，不能覆盖。
        binding.safe(binding.put(local_client, (target[0], str(other))), 200)
        return target[1]
    monkeypatch.setattr(workspace, 'select_directory', choose)
    binding.safe(post(local_client, target), 409, 'workspace_already_bound')
    assert all(session.closed for session in sessions)
