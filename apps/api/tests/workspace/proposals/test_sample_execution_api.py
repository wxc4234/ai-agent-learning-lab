"""真实 local HTTP、隔离 PostgreSQL 和自有样例验证应用入口。"""

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.main import app
from app.models import User, Workspace
from app.routers.workspace import execution as routes
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.workspace.proposals import file_edit_proposal_execution as execution
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from tests.local.test_local_mode import HEADERS, local_client
from tests.workspace.samples.test_sample_proposal_execution import ready, state
from tests.workspace.samples.test_task_sample_binding import setup, target

__all__ = ['local_client', 'ready', 'setup', 'target']


@pytest.fixture
def endpoint(ready, local_client, engine):
    bindings, scope, identity, path = ready
    # 使用真实 CurrentUser；仅将隔离夹具的所有者映射为本机身份。
    with Session(engine) as session, session.begin():
        session.get(User, scope['user_id']).external_id = LOCAL_USER_ID
    app.dependency_overrides[get_sample_bindings] = lambda: bindings
    url = (f"/workspaces/{scope['workspace_id']}/tasks/{scope['task_id']}"
           f"/file-edit-proposals/{identity['proposal_id']}/apply")
    try:
        yield local_client, url, path, identity
    finally:
        app.dependency_overrides.pop(get_sample_bindings, None)


def send(endpoint, **kwargs):
    client, url, _, _ = endpoint
    return client.post(url, **({'headers': HEADERS, 'json': {'action': 'apply'}} | kwargs))


def safe(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    assert 'PRIVATE' not in response.text and 'bound_root' not in response.text
    assert 'application_token' not in response.text and 'proposed_content' not in response.text


def test_real_apply_and_repeat_never_writes_twice(endpoint, engine, monkeypatch):
    writer = execution.replace_workspace_text_file
    calls = []
    def tracked(**kwargs):
        calls.append(1)
        return writer(**kwargs)
    monkeypatch.setattr(execution, 'replace_workspace_text_file', tracked)
    response = send(endpoint)
    safe(response, 200)
    body = response.json()
    assert set(body) == {'workspace_id', 'task_id', 'proposal_id', 'file_status',
                         'application_status', 'code', 'cleanup_complete'}
    assert body['file_status'] == 'replaced'
    assert body['application_status'] == state(engine) == 'applied'
    assert (endpoint[2] / 'example.txt').read_bytes() == b'new\n'
    send(endpoint)
    assert calls == [1]


@pytest.mark.parametrize('payload', [{}, {'action': 'retry'}, {'action': 'apply', 'path': '/PRIVATE'},
                                   {'action': 'apply', 'user_id': 1}, {'action': 'apply', 'skip_checks': True}, None])
def test_invalid_body_never_executes(endpoint, monkeypatch, payload):
    monkeypatch.setattr(routes, 'execute_sample_proposal', lambda *a, **kw: pytest.fail('must not execute'))
    safe(send(endpoint, json=payload, headers=HEADERS | {"Content-Type": "application/json"}), 422)


@pytest.mark.parametrize('change', ['query', 'identifier', 'origin', 'token', 'host', 'content_type'])
def test_request_boundary_before_execution(endpoint, monkeypatch, change):
    monkeypatch.setattr(routes, 'execute_sample_proposal', lambda *a, **kw: pytest.fail('must not execute'))
    client, url, _, _ = endpoint
    headers = dict(HEADERS)
    status = 422
    if change == 'query':
        url += '?path=/PRIVATE'
    elif change == 'identifier':
        url = url.replace(endpoint[3]['proposal_id'], 'INVALID')
    elif change in ('origin', 'token', 'host'):
        headers[{'origin': 'Origin', 'token': 'X-Local-Runtime-Token', 'host': 'Host'}[change]] = 'evil'
        status = 403
    else:
        headers['Content-Type'] = 'text/plain'
        status = 415
    safe(client.post(url, headers=headers, json={'action': 'apply'}), status)


def test_empty_registry_does_not_adopt_existing_directory(endpoint, monkeypatch, engine):
    app.dependency_overrides[get_sample_bindings] = TaskSampleBindings
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kw: pytest.fail('must not write'))
    safe(send(endpoint), 409)
    assert state(engine) == 'idle'
    assert (endpoint[2] / 'example.txt').read_bytes() == b'old\n'


@pytest.mark.parametrize('timing', ['before_commit', 'after_commit'])
def test_registration_unknown_is_not_reported_as_not_executed(endpoint, setup, engine, monkeypatch, timing):
    finish = execution.finish_task_file_edit_proposal
    def fail(*args):
        raise RuntimeError('PRIVATE commit detail')
    def wrapped(**kwargs):
        # 故障注入只覆盖最终登记事务，文件替换已经完成。
        event.listen(setup[2], timing, fail)
        try:
            return finish(**kwargs)
        finally:
            event.remove(setup[2], timing, fail)
    monkeypatch.setattr(execution, 'finish_task_file_edit_proposal', wrapped)
    response = send(endpoint)
    safe(response, 200)
    assert response.json()['application_status'] == 'unknown'
    assert response.json()['file_status'] == 'replaced'
    assert state(engine) == ('running' if timing == 'before_commit' else 'applied')
    assert (endpoint[2] / 'example.txt').read_bytes() == b'new\n'
    safe(send(endpoint), 409)


@pytest.mark.parametrize('phase', ['service', 'response'])
def test_unexpected_failure_has_safe_uncertain_error(endpoint, monkeypatch, phase):
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE filesystem and database information')
    name = 'execute_sample_proposal' if phase == 'service' else 'build_proposal_execution_response'
    monkeypatch.setattr(routes, name, fail)
    response = send(endpoint)
    safe(response, 500)
    assert response.json()['code'] == 'proposal_execution_uncertain'
    if phase == 'response':
        assert (endpoint[2] / 'example.txt').read_bytes() == b'new\n'


@pytest.mark.parametrize('resource', ['workspace_id', 'task_id', 'proposal_id'])
def test_wrong_resource_never_writes(endpoint, resource, monkeypatch):
    client, url, path, identity = endpoint
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kw: pytest.fail('must not write'))
    safe(client.post(url.replace(identity[resource], 'f' * 32), headers=HEADERS,
                     json={'action': 'apply'}), 404 if resource == 'proposal_id' else 409)
    assert (path / 'example.txt').read_bytes() == b'old\n'


def test_account_mode_rejects_sample_execution(endpoint, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'app_mode', 'account')
    monkeypatch.setattr(routes, 'execute_sample_proposal', lambda *a, **kw: pytest.fail('must not execute'))
    safe(send(endpoint), 403)



def test_binding_reauthorizes_changed_owner(endpoint, target, engine, monkeypatch):
    with Session(engine) as session, session.begin():
        workspace = session.query(Workspace).filter_by(external_id=endpoint[3]['workspace_id']).one()
        workspace.user_id = target['other_id']
    monkeypatch.setattr(execution, 'replace_workspace_text_file', lambda **kw: pytest.fail('must not write'))
    safe(send(endpoint), 404)
    assert (endpoint[2] / 'example.txt').read_bytes() == b'old\n'
