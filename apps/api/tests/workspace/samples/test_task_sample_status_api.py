"""真实local GET与隔离PostgreSQL验证样例登记查询HTTP边界。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.main import app
from app.models import User, Workspace
from app.services.auth.local_identity import LOCAL_USER_ID
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError
from app.services.workspace.samples.temporary_proposal_sample import TemporarySampleError
from tests.local.test_local_mode import HEADERS, local_client
from tests.workspace.samples.test_task_sample_binding import setup, target

__all__ = ['local_client', 'setup', 'target']


@pytest.fixture
def endpoint(local_client, setup, engine):
    bindings, scope, _, _ = setup
    with Session(engine) as session, session.begin():
        session.get(User, scope['user_id']).external_id = LOCAL_USER_ID
    app.dependency_overrides[get_sample_bindings] = lambda: bindings
    try:
        yield local_client, f"/workspaces/{scope['workspace_id']}/tasks/{scope['task_id']}/sample-status"
    finally:
        app.dependency_overrides.pop(get_sample_bindings, None)


def read(endpoint, **kwargs):
    client, url = endpoint
    return client.get(url, **({'headers': HEADERS} | kwargs))


def safe(response, expected, code=None):
    assert response.status_code == expected, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    assert 'PRIVATE' not in response.text
    assert 'root' not in response.json() and 'handle' not in response.json()
    if code:
        assert response.json()['code'] == code


@pytest.mark.parametrize('state', ['missing', 'ready', 'busy', 'sealed'])
def test_real_states_only_return_public_fields(endpoint, setup, state):
    bindings, scope, _, _ = setup
    if state != 'missing':
        bindings.bind(**scope)
    if state == 'sealed':
        with pytest.raises(RuntimeError), bindings.borrow(**scope):
            raise RuntimeError('PRIVATE')
    if state == 'busy':
        with bindings.borrow(**scope):
            response = read(endpoint)
    else:
        response = read(endpoint)
    safe(response, 200)
    assert response.json() == {key: scope[key] for key in ('workspace_id', 'task_id')} | {
        'status': state, 'sealed_reason': 'unavailable' if state == 'sealed' else None,
    }


def test_lost_process_registration_reports_sealed_over_http(endpoint, setup):
    bindings, scope, _, _ = setup
    bindings.bind(**scope)
    # 模拟重启后新的进程登记：数据库来源仍在，但可信句柄只在旧实例中。
    app.dependency_overrides[get_sample_bindings] = lambda: type(bindings)()
    response = read(endpoint)
    safe(response, 200)
    assert response.json() == {
        'workspace_id': scope['workspace_id'],
        'task_id': scope['task_id'],
        'status': 'sealed',
        'sealed_reason': 'unavailable',
    }


def test_cleanup_pending_reports_sealed_over_http(endpoint, setup, engine):
    bindings, scope, _, _ = setup
    bindings.bind(**scope)
    with Session(engine) as session:
        path = Path(session.scalar(select(Workspace.root_path)))
    (path / 'unknown').write_bytes(b'keep')
    with pytest.raises(TemporarySampleError):
        bindings.close(**scope)
    response = read(endpoint)
    safe(response, 200)
    assert response.json() == {
        'workspace_id': scope['workspace_id'],
        'task_id': scope['task_id'],
        'status': 'sealed',
        'sealed_reason': 'cleanup_pending',
    }


def test_pending_reason_is_hidden_from_sibling_task(endpoint, setup, engine):
    bindings, scope, _, _ = setup
    bindings.bind(**scope)
    with Session(engine) as session:
        path = Path(session.scalar(select(Workspace.root_path)))
    (path / 'unknown').write_bytes(b'keep')
    with pytest.raises(TemporarySampleError):
        bindings.close(**scope)

    client, url = endpoint
    sibling_url = url.replace(scope['task_id'], 'd' * 32)
    response = client.get(sibling_url, headers=HEADERS)
    safe(response, 200)
    assert response.json() == {
        'workspace_id': scope['workspace_id'],
        'task_id': 'd' * 32,
        'status': 'sealed',
        'sealed_reason': 'unavailable',
    }


@pytest.mark.parametrize(('status', 'reason'), [
    ('sealed', None), ('ready', 'cleanup_pending'), ('missing', 'unavailable'),
    ('sealed', 'PRIVATE'),
])
def test_invalid_internal_reason_combinations_fail_closed(endpoint, setup, monkeypatch, status, reason):
    monkeypatch.setattr(
        setup[0], 'read_status',
        lambda **kw: SimpleNamespace(status=status, sealed_reason=reason, root_path='PRIVATE'),
    )
    response = read(endpoint)
    safe(response, 500, 'sample_status_read_failed')
    assert set(response.json()) == {'code', 'message'}


@pytest.mark.parametrize('kind', ['workspace', 'task', 'foreign'])
def test_missing_registration_still_requires_ownership(endpoint, setup, target, kind):
    client, url = endpoint
    if kind == 'foreign':
        # 合法本机身份读取另一个用户的项目，不能降级成missing。
        from app.services.workspace.samples import task_sample_binding as module
        factory = module.SessionLocal
        with factory() as session, session.begin():
            from app.models import Workspace
            from sqlalchemy import select
            session.scalar(select(Workspace)).user_id = target['other_id']
    else:
        url = url.replace(setup[1][kind + '_id'], 'f' * 32)
    safe(client.get(url, headers=HEADERS), 404, 'workspace_not_accessible')


@pytest.mark.parametrize('kind', ['query', 'body', 'workspace', 'task'])
def test_invalid_input_never_queries(endpoint, setup, monkeypatch, kind):
    client, url = endpoint
    monkeypatch.setattr(setup[0], 'read_status', lambda **kw: pytest.fail('must not query'))
    kwargs = {}
    if kind == 'query':
        url += '?user_id=1'
    elif kind == 'body':
        kwargs['content'] = b'{"path":"PRIVATE"}'
    else:
        url = url.replace(setup[1][kind + '_id'], 'INVALID')
    safe(client.request('GET', url, headers=HEADERS, **kwargs), 422, 'invalid_sample_status_input')


@pytest.mark.parametrize('kind', ['token', 'host', 'origin', 'mode'])
def test_local_boundary_before_query(endpoint, setup, monkeypatch, kind):
    headers = dict(HEADERS)
    if kind == 'mode':
        monkeypatch.setattr(settings, 'app_mode', 'account')
    else:
        headers[{'token': 'X-Local-Runtime-Token', 'host': 'Host', 'origin': 'Origin'}[kind]] = 'PRIVATE'
    monkeypatch.setattr(setup[0], 'read_status', lambda **kw: pytest.fail('must not query'))
    safe(read(endpoint, headers=headers), 403)


def test_read_allows_absent_origin_but_requires_internal_token(endpoint):
    safe(read(endpoint, headers={k: v for k, v in HEADERS.items() if k != 'Origin'}), 200)


@pytest.mark.parametrize('kind', ['database', 'binding', 'invalid_state'])
def test_failures_are_not_missing_or_ready(endpoint, setup, monkeypatch, kind):
    def fail(**kwargs):
        if kind == 'invalid_state':
            return SimpleNamespace(status='PRIVATE')
        if kind == 'binding':
            raise TaskSampleBindingError('PRIVATE')
        raise RuntimeError('PRIVATE database path')
    monkeypatch.setattr(setup[0], 'read_status', fail)
    response = read(endpoint)
    safe(response, 500, 'sample_status_read_failed')
    assert set(response.json()) == {'code', 'message'}


def test_query_does_not_create_or_borrow(endpoint, setup, monkeypatch):
    for name in ('create', 'borrow'):
        monkeypatch.setattr(setup[0]._registry, name, lambda *a, **kw: pytest.fail('must not touch registry'))
    safe(read(endpoint), 200)
