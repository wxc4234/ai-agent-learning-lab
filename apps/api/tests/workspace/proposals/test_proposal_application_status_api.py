"""应用状态GET：真实local身份、隔离查询和统一HTTP安全边界。"""

from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import FileEditProposal, Workspace
from app.routers.workspace import proposals as routes
from app.services.workspace.proposals import file_edit_proposal_application_query as service
from tests.local.test_local_mode import HEADERS
from tests.workspace.proposals import test_file_edit_proposal_api as detail_tests

local_client = detail_tests.local_client
saved = detail_tests.saved
safe = detail_tests.safe


@pytest.fixture
def ready(saved, engine, monkeypatch):
    sessions = []
    class ReadSession(Session):
        def commit(self):
            pytest.fail('query must not commit')
        def close(self):
            super().close()
            self.closed = True
    def factory():
        session = sessionmaker(bind=engine, class_=ReadSession)()
        sessions.append(session)
        return session
    monkeypatch.setattr(service, 'SessionLocal', factory)
    yield saved
    assert all(s.closed and not s.in_transaction() for s in sessions)


def get(client, path, **kwargs):
    return client.get(path + '/application-status', headers=HEADERS, **kwargs)


@pytest.mark.parametrize('state', ['idle', 'running', 'applied', 'not_applied', 'uncertain'])
def test_public_five_states_and_file_unchanged(local_client, ready, engine, state):
    path, file, proposal = ready
    with Session(engine) as session, session.begin():
        p = session.scalar(select(FileEditProposal))
        p.status = 'approved'
        p.application_status = state
        p.application_token = None if state == 'idle' else '0123456789abcdef' * 2
    response = local_client.get(path + '/application-status', headers=HEADERS | {
        'X-User-ID': '9999', 'Cookie': 'agent_session=PRIVATE', 'Authorization': 'PRIVATE',
    })
    safe(response, 200)
    assert response.json() == {
        'proposal_id': proposal.proposal_id, 'workspace_id': proposal.workspace_id,
        'task_id': proposal.task_id, 'application_status': state,
    }
    assert file.read_bytes() == b'old\r\n'
    assert 'set-cookie' not in response.headers and str(file.parent) not in response.text
    with Session(engine) as session:
        assert session.scalar(select(FileEditProposal.application_status)) == state


@pytest.mark.parametrize('kind', ['missing-project', 'missing-task', 'missing-proposal',
                                  'sibling', 'owner', 'conversation', 'deleted'])
def test_not_found_and_unauthorized_use_same_response(local_client, ready, engine, monkeypatch, kind):
    original = local_client.get
    monkeypatch.setattr(local_client, 'get', lambda url, **kw: original(url + '/application-status', **kw))
    detail_tests.test_unknown_and_inaccessible_are_same_404(local_client, ready, engine, kind)


@pytest.mark.parametrize('index', [2, 4, 6])
@pytest.mark.parametrize('invalid', ['PRIVATE', 'A' * 32, 'a' * 33])
def test_strict_identifiers(local_client, ready, monkeypatch, index, invalid):
    parts = ready[0].split('/')
    parts[index] = invalid
    monkeypatch.setattr(routes, 'get_task_file_edit_proposal_application_status',
                        lambda **kw: pytest.fail('invalid identifier reached service'))
    response = get(local_client, '/'.join(parts))
    safe(response, 422)
    assert response.json()['code'] == 'invalid_proposal_application_status_input'


@pytest.mark.parametrize('query', [{'user_id': 'PRIVATE'}, {'proposal_id': 'PRIVATE'}, {'retry': 'true'}])
def test_query_parameters_rejected(local_client, ready, monkeypatch, query):
    monkeypatch.setattr(routes, 'get_task_file_edit_proposal_application_status',
                        lambda **kw: pytest.fail('query reached service'))
    response = get(local_client, ready[0], params=query)
    safe(response, 422)
    assert response.json()['code'] == 'invalid_proposal_application_status_input'


@pytest.mark.parametrize('kind', ['mode', 'token', 'host', 'origin'])
def test_local_boundary(local_client, ready, monkeypatch, kind):
    headers = dict(HEADERS)
    if kind == 'mode':
        monkeypatch.setattr(settings, 'app_mode', 'account')
    elif kind == 'token':
        headers['X-Local-Runtime-Token'] = 'PRIVATE'
    elif kind == 'host':
        headers['Host'] = 'evil.example'
    else:
        headers['Origin'] = 'https://evil.example'
    monkeypatch.setattr(routes, 'get_task_file_edit_proposal_application_status',
                        lambda **kw: pytest.fail('boundary failure reached service'))
    safe(local_client.get(ready[0] + '/application-status', headers=headers), 403)


@pytest.mark.parametrize('kind', ['exception', 'unknown', 'bad-id', 'response'])
def test_unknown_failure_and_serialization_have_specific_safe_code(local_client, ready, monkeypatch, caplog, kind):
    original = routes.get_task_file_edit_proposal_application_status
    def fail(**kwargs):
        if kind == 'exception':
            raise RuntimeError('PRIVATE SQL path')
        result = original(**kwargs)
        if kind == 'unknown':
            return replace(result, application_status='PRIVATE')
        return replace(result, proposal_id='PRIVATE')
    if kind == 'response':
        def fail_response(*args, **kwargs):
            raise RuntimeError('PRIVATE serialization')
        monkeypatch.setattr(routes.FileEditProposalApplicationStatusResponse, 'model_validate', fail_response)
    else:
        monkeypatch.setattr(routes, 'get_task_file_edit_proposal_application_status', fail)
    response = get(local_client, ready[0])
    safe(response, 500)
    assert response.json()['code'] == 'proposal_application_status_read_failed'
    assert 'PRIVATE' not in caplog.text


def test_missing_file_and_changed_binding_are_queryable(local_client, ready, engine):
    ready[1].unlink()
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = None
    safe(get(local_client, ready[0]), 200)


def test_openapi_contract_and_routes_are_unique(local_client):
    schema = local_client.get('/openapi.json', headers=HEADERS).json()
    path = '/workspaces/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}/application-status'
    operation = schema['paths'][path]['get']
    assert set(operation['responses']) == {'200', '401', '403', '404', '422', '500'}
    properties = schema['components']['schemas']['FileEditProposalApplicationStatusResponse']['properties']
    assert set(properties) == {'proposal_id', 'workspace_id', 'task_id', 'application_status'}
    assert properties['application_status']['enum'] == ['idle', 'running', 'applied', 'not_applied', 'uncertain']
    keys = [(method, route.path) for route in routes.router.routes for method in route.methods]
    assert len(keys) == len(set(keys))
