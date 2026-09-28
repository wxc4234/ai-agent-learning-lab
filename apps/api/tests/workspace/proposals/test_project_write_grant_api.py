"""本地许可HTTP契约，真实身份/PG事务/临时文件，不执行文件写入。"""

from contextlib import contextmanager
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import main
from app.config import settings
from app.models import FileEditProposal, Workspace
from app.routers.workspace import project_write_grants as routes
from app.services.workspace.proposals import project_write_grants as service
from app.services.workspace.proposals import project_write_snapshot as snapshots
from tests.assertions import require_value
from tests.local.test_local_mode import HEADERS
from tests.workspace.proposals.test_file_edit_proposal_api import local_client, saved

__all__ = ['local_client', 'saved']


@pytest.fixture
def ready(saved, engine, monkeypatch):
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, 'SessionLocal', factory)
    monkeypatch.setattr(snapshots, 'SessionLocal', factory)
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(FileEditProposal))).status = 'approved'
    return saved[0] + '/write-grant', saved[1]


def check(response, status, code=None):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert 'PRIVATE' not in response.text
    if code:
        assert response.json()['code'] == code
        assert set(response.json()) == {'code', 'message'}


def issue(client, path):
    return client.post(path, headers=HEADERS, json={'action': 'grant'})


def test_real_lifecycle_public_projection_and_conflicts(local_client, ready, engine):
    path, file = ready
    before = file.read_bytes(), file.stat().st_mtime_ns
    first = local_client.get(path, headers=HEADERS)
    check(first, 200)
    assert first.json()['grant'] is None
    issued = issue(local_client, path)
    check(issued, 201)
    data = issued.json()
    assert set(data) == {'workspace_id', 'task_id', 'proposal_id', 'grant'}
    assert set(data['grant']) == {'grant_id', 'revision', 'status'}
    assert data['grant']['status'] == 'enabled' and data['grant']['revision'] == 1
    assert str(file.parent) not in issued.text
    assert local_client.get(path, headers=HEADERS).json() == data
    check(issue(local_client, path), 409, 'project_write_grant_conflict')
    payload = {key: data['grant'][key] for key in ('grant_id', 'revision')}
    revoked = local_client.post(path + '/revoke', headers=HEADERS, json=payload)
    check(revoked, 200)
    assert revoked.json()['grant'] == {**payload, 'revision': 2, 'status': 'revoked'}
    assert local_client.get(path, headers=HEADERS).json() == revoked.json()
    check(local_client.post(path + '/revoke', headers=HEADERS, json=payload), 409, 'project_write_grant_conflict')
    check(issue(local_client, path), 409, 'project_write_grant_conflict')
    assert (file.read_bytes(), file.stat().st_mtime_ns) == before
    with Session(engine) as session:
        proposal = require_value(session.scalar(select(FileEditProposal)))
        assert (proposal.application_status, proposal.application_token) == ('idle', None)


@pytest.mark.parametrize('body', [{}, {'action': 'apply'}, {'action': True}, {'action': 'grant', 'target': 'PRIVATE'}, [], None])
def test_issue_strict_body(local_client, ready, monkeypatch, body):
    monkeypatch.setattr(routes.ProjectWriteGrantService, 'issue', lambda *a, **kw: pytest.fail('invalid input'))
    response = local_client.post(ready[0], headers=HEADERS | {'Content-Type': 'application/json'},
                                 content='null' if body is None else json.dumps(body))
    check(response, 422, 'invalid_project_write_grant_input')


@pytest.mark.parametrize('body', [
    {}, {'grant_id': 'f'*32, 'revision': True}, {'grant_id': 'f'*32, 'revision': '1'},
    {'grant_id': 'f'*32, 'revision': 0}, {'grant_id': 'f'*32, 'revision': 2**63},
    {'grant_id': 'PRIVATE', 'revision': 1}, {'grant_id': 'f'*32, 'revision': 1, 'user_id': 1},
])
def test_revoke_strict_body(local_client, ready, monkeypatch, body):
    monkeypatch.setattr(routes.ProjectWriteGrantService, 'revoke', lambda *a, **kw: pytest.fail('invalid input'))
    check(local_client.post(ready[0] + '/revoke', headers=HEADERS, json=body), 422, 'invalid_project_write_grant_input')


@pytest.mark.parametrize('operation', ['read', 'issue', 'revoke'])
def test_query_and_path_validation(local_client, ready, operation):
    method = 'GET' if operation == 'read' else 'POST'
    path = ready[0] + ('/revoke' if operation == 'revoke' else '')
    body = {'action': 'grant'} if operation == 'issue' else {'grant_id': 'f'*32, 'revision': 1}
    kwargs = {} if method == 'GET' else {'json': body}
    check(local_client.request(method, path + '?user_id=PRIVATE', headers=HEADERS, **kwargs), 422, 'invalid_project_write_grant_input')
    parts = path.split('/')
    parts[2] = 'PRIVATE'
    check(local_client.request(method, '/'.join(parts), headers=HEADERS, **kwargs), 422, 'invalid_project_write_grant_input')


def test_read_rejects_body(local_client, ready):
    check(local_client.request('GET', ready[0], headers=HEADERS, content='PRIVATE'), 422, 'invalid_project_write_grant_input')


@pytest.mark.parametrize('operation', ['read', 'issue', 'revoke'])
def test_mode_gate_precedes_service(local_client, ready, monkeypatch, operation):
    monkeypatch.setattr(settings, 'app_mode', 'account')
    monkeypatch.setattr(routes, '_host', lambda r: pytest.fail('must reject before service'))
    method = 'GET' if operation == 'read' else 'POST'
    path = ready[0] + ('/revoke' if operation == 'revoke' else '')
    check(local_client.request(method, path, headers=HEADERS, json={}), 403, 'local_mode_required')


@pytest.mark.parametrize('headers', [{}, HEADERS | {'Origin': 'https://evil.test'}, HEADERS | {'X-Local-Runtime-Token': 'bad'}, HEADERS | {'Host': 'evil.test'}])
def test_local_access_boundary(local_client, ready, headers):
    check(local_client.post(ready[0], headers=headers, json={'action': 'grant'}), 403)


def test_write_origin_and_content_type(local_client, ready):
    check(local_client.post(ready[0], headers={k:v for k,v in HEADERS.items() if k != 'Origin'}, json={'action':'grant'}), 403, 'workspace_origin_rejected')
    check(local_client.post(ready[0], headers=HEADERS, content='PRIVATE'), 415, 'unsupported_workspace_content_type')


@pytest.mark.parametrize('operation', ['read', 'issue', 'revoke'])
@pytest.mark.parametrize('changed', ['unknown', 'unknown_workspace', 'unknown_task', 'owner'])
def test_resource_ownership(local_client, ready, engine, operation, changed):
    path = ready[0]
    if changed.startswith('unknown'):
        parts = path.split('/')
        index = {'unknown': -2, 'unknown_workspace': 2, 'unknown_task': 4}[changed]
        parts[index] = 'f' * 32
        path = '/'.join(parts)
    else:
        from app.models import User
        with Session(engine) as session, session.begin():
            other = User(external_id='other')
            session.add(other)
            session.flush()
            require_value(session.scalar(select(Workspace))).user_id = other.id
    if operation == 'read':
        response = local_client.get(path, headers=HEADERS)
    elif operation == 'issue':
        response = issue(local_client, path)
    else:
        response = local_client.post(path + '/revoke', headers=HEADERS, json={'grant_id': 'f'*32, 'revision': 1})
    check(response, 404, 'workspace_not_accessible')


@pytest.mark.parametrize('operation', ['read', 'issue', 'revoke'])
def test_missing_host_is_not_recreated(local_client, ready, operation):
    host = main.app.state.project_write_grants
    del main.app.state.project_write_grants
    try:
        if operation == 'read':
            response = local_client.get(ready[0], headers=HEADERS)
        elif operation == 'issue':
            response = issue(local_client, ready[0])
        else:
            response = local_client.post(ready[0] + '/revoke', headers=HEADERS, json={'grant_id': 'f'*32, 'revision': 1})
        check(response, 500, 'project_write_grant_read_failed' if operation == 'read' else 'project_write_grant_uncertain')
        assert not hasattr(main.app.state, 'project_write_grants')
    finally:
        main.app.state.project_write_grants = host


@pytest.mark.parametrize('operation', ['issue', 'revoke'])
@pytest.mark.parametrize('committed', [False, True])
def test_commit_confirmation_lost_is_queryable_without_replay(local_client, ready, engine, monkeypatch, operation, committed):
    path = ready[0]
    grant = issue(local_client, path).json()['grant'] if operation == 'revoke' else None
    calls = []
    class Factory:
        @staticmethod
        @contextmanager
        def begin():
            calls.append(1)
            with Session(engine) as session:
                yield session
                if committed:
                    session.commit()
                raise OSError('PRIVATE database result lost')
    with monkeypatch.context() as patch:
        patch.setattr(service, 'SessionLocal', Factory)
        if grant:
            response = local_client.post(path + '/revoke', headers=HEADERS,
                                         json={k: grant[k] for k in ('grant_id', 'revision')})
        else:
            response = issue(local_client, path)
        check(response, 500, 'project_write_grant_uncertain')
    assert calls == [1]
    result = local_client.get(path, headers=HEADERS)
    check(result, 200)
    observed = result.json()['grant']
    if operation == 'issue':
        assert (observed is not None) == committed
    else:
        assert observed['status'] == ('revoked' if committed else 'enabled')


@pytest.mark.parametrize('operation', ['issue', 'read'])
def test_response_failure_is_safe_and_not_replayed(local_client, ready, monkeypatch, operation):
    def fail(*args):
        raise ValueError('PRIVATE response failure')
    with monkeypatch.context() as patch:
        patch.setattr(routes, '_response', fail)
        response = issue(local_client, ready[0]) if operation == 'issue' else local_client.get(ready[0], headers=HEADERS)
    check(response, 500, 'project_write_grant_uncertain' if operation == 'issue' else 'project_write_grant_read_failed')
    assert (local_client.get(ready[0], headers=HEADERS).json()['grant'] is not None) == (operation == 'issue')


def test_request_reuses_application_host_and_ignores_spoofed_identity(local_client, ready, engine, monkeypatch):
    host = main.app.state.project_write_grants
    seen = []
    get_host = routes._host
    def capture(request):
        result = get_host(request)
        seen.append(result)
        return result
    monkeypatch.setattr(routes, '_host', capture)
    response = local_client.post(ready[0], headers=HEADERS | {'X-User-ID': '999999', 'Cookie': 'agent_session=PRIVATE'}, json={'action':'grant'})
    check(response, 201)
    check(local_client.get(ready[0], headers=HEADERS), 200)
    from app.models import ProjectWriteGrantRecord
    with Session(engine) as session:
        record = require_value(session.scalar(select(ProjectWriteGrantRecord)))
        workspace = require_value(session.scalar(select(Workspace)))
        assert record.target['user_id'] == workspace.user_id
        assert record.target['runtime_id'] == host._reader._runtime_id
    assert seen == [host, host]


def test_openapi_has_only_public_grant_fields(local_client):
    schema = local_client.get('/openapi.json', headers=HEADERS).json()
    path = '/workspaces' + routes.BASE
    assert set(schema['paths'][path]) == {'get', 'post'}
    assert '201' in schema['paths'][path]['post']['responses']
    assert '500' in schema['paths'][path]['get']['responses']
    public = schema['components']['schemas']['ProjectWriteGrantPublicRecord']
    assert set(public['properties']) == {'grant_id', 'revision', 'status'}
    assert not public['additionalProperties']


@pytest.mark.parametrize('kind', ['missing', 'revision', 'id'])
def test_revoke_conflicts_do_not_change_record(local_client, ready, kind):
    grant = issue(local_client, ready[0]).json()['grant'] if kind != 'missing' else None
    payload = {'grant_id': grant['grant_id'] if grant else 'f'*32, 'revision': 1}
    if kind == 'revision':
        payload['revision'] = 2
    elif kind == 'id':
        payload['grant_id'] = 'f'*32
    check(local_client.post(ready[0] + '/revoke', headers=HEADERS, json=payload), 409, 'project_write_grant_conflict')
    assert local_client.get(ready[0], headers=HEADERS).json()['grant'] == grant
