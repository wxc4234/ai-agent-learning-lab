"""前置检查HTTP只读契约：真实PG、临时文件及应用宿主。"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main
from app.config import settings
from app.models import FileEditProposal, ProjectWriteGrantRecord, User, Workspace
from app.services.workspace.proposals import project_write_grants as service
from app.services.workspace.proposals.project_write_policy import ProjectWriteDecision
from tests.assertions import require_value
from tests.local.test_local_mode import HEADERS
from tests.workspace.proposals.test_project_write_grant_api import check, issue, local_client, ready, saved

__all__ = ['local_client', 'ready', 'saved']
BODY = {'grant_id': 'f' * 32, 'revision': 1, 'apply_requested': True}


def assess(client, path, body):
    return client.post(path + '/assessment', headers=HEADERS, json=body)


def issued(client, path):
    response = issue(client, path)
    check(response, 201)
    grant = response.json()['grant']
    return {key: grant[key] for key in ('grant_id', 'revision')} | {'apply_requested': True}


def snapshot(engine, file):
    # 对账业务字段及文件对象；不把读取造成的atime变化当作内容写入。
    with Session(engine) as session:
        proposal = require_value(session.scalar(select(FileEditProposal)))
        record = session.scalar(select(ProjectWriteGrantRecord))
        state = (proposal.status, proposal.application_status, proposal.application_token,
                 None if record is None else (record.grant_id, record.revision, record.enabled, dict(record.target)))
    stat = file.stat()
    return state, file.read_bytes(), stat.st_ino, stat.st_mtime_ns


def test_readonly_valid_target_and_repeat(local_client, ready, engine):
    path, file = ready
    body = issued(local_client, path)
    before = snapshot(engine, file)
    for _ in range(2):
        response = assess(local_client, path, body)
        check(response, 200)
        parts = path.split('/')
        assert response.json() == {
            'workspace_id': parts[2], 'task_id': parts[4], 'proposal_id': parts[6],
            'result': 'exclusive_access_unconfirmed',
        }
        assert str(file.parent) not in response.text
    assert snapshot(engine, file) == before
    assert before[0][1:3] == ('idle', None)


@pytest.mark.parametrize('state,expected', [
    ('missing', 'grant_missing'), ('intent', 'apply_not_requested'),
    ('revoked', 'grant_revoked'), ('revision', 'grant_changed'), ('id', 'grant_changed'),
    ('pending', 'proposal_not_approved'), ('running', 'application_not_idle'),
    ('applied', 'application_not_idle'), ('not_applied', 'application_not_idle'),
    ('uncertain', 'application_not_idle'),
])
def test_known_denials_are_readonly(local_client, ready, engine, state, expected):
    path, file = ready
    body = dict(BODY) if state == 'missing' else issued(local_client, path)
    if state == 'intent':
        body['apply_requested'] = False
    elif state == 'revision':
        body['revision'] = 2
    elif state == 'id':
        body['grant_id'] = 'e' * 32
    elif state == 'revoked':
        check(local_client.post(path + '/revoke', headers=HEADERS,
                               json={key: body[key] for key in ('grant_id', 'revision')}), 200)
    elif state in ('pending', 'running', 'applied', 'not_applied', 'uncertain'):
        with Session(engine) as session, session.begin():
            proposal = require_value(session.scalar(select(FileEditProposal)))
            if state == 'pending':
                proposal.status = state
            else:
                proposal.application_status = state
                proposal.application_token = 'a' * 32
    before = snapshot(engine, file)
    response = assess(local_client, path, body)
    check(response, 200)
    assert response.json()['result'] == expected
    assert snapshot(engine, file) == before


@pytest.mark.parametrize('change', ['host', 'binding', 'inode'])
def test_changed_target(local_client, ready, engine, monkeypatch, change):
    path, file = ready
    body = issued(local_client, path)
    if change == 'host':
        monkeypatch.setattr(main.app.state, 'project_write_grants', service.ProjectWriteGrantService())
    elif change == 'binding':
        with Session(engine) as session, session.begin():
            require_value(session.scalar(select(Workspace))).binding_revision += 1
    else:
        old = file.with_suffix('.old')
        file.rename(old)
        file.write_bytes(old.read_bytes())
    before = snapshot(engine, file)
    response = assess(local_client, path, body)
    check(response, 200)
    assert response.json()['result'] == 'target_changed'
    assert snapshot(engine, file) == before


@pytest.mark.parametrize('body', [
    {}, [], None, BODY | {'revision': True}, BODY | {'revision': '1'},
    BODY | {'revision': 0}, BODY | {'revision': 2**63}, BODY | {'grant_id': 'bad'},
    BODY | {'grant_id': 'f' * 32 + '\n'}, BODY | {'apply_requested': 1},
    BODY | {'apply_requested': 'true'}, {key: value for key, value in BODY.items() if key != 'apply_requested'},
    *[BODY | {key: True} for key in ('authorized', 'target', 'current_sha256', 'exclusive_access_confirmed', 'user_id')],
])
def test_strict_input_before_service(local_client, ready, monkeypatch, body):
    monkeypatch.setattr(service.ProjectWriteGrantService, 'assess', lambda *a, **kw: pytest.fail('invalid input'))
    response = local_client.post(ready[0] + '/assessment', headers=HEADERS | {'Content-Type': 'application/json'},
                                 content=json.dumps(body))
    check(response, 422, 'invalid_project_write_assessment_input')


def test_query_path_and_json_errors(local_client, ready):
    endpoint = ready[0] + '/assessment'
    for path, content in [(endpoint + '?target=PRIVATE', json.dumps(BODY)),
                          (endpoint.replace(endpoint.split('/')[2], 'PRIVATE'), json.dumps(BODY)),
                          (endpoint, '{PRIVATE')]:
        check(local_client.post(path, headers=HEADERS | {'Content-Type': 'application/json'}, content=content),
              422, 'invalid_project_write_assessment_input')


@pytest.mark.parametrize('changed', ['workspace', 'task', 'proposal', 'owner'])
def test_ownership_precedes_observation(local_client, ready, engine, monkeypatch, changed):
    path = ready[0]
    body = issued(local_client, path)
    if changed == 'owner':
        with Session(engine) as session, session.begin():
            other = User(external_id='other')
            session.add(other)
            session.flush()
            require_value(session.scalar(select(Workspace))).user_id = other.id
    else:
        parts = path.split('/')
        parts[{'workspace': 2, 'task': 4, 'proposal': 6}[changed]] = 'e' * 32
        path = '/'.join(parts)
    monkeypatch.setattr(main.app.state.project_write_grants._reader, 'read', lambda **kw: pytest.fail('unauthorized IO'))
    check(assess(local_client, path, body), 404, 'workspace_not_accessible')


def test_mode_origin_and_json_boundary(local_client, ready, monkeypatch):
    endpoint = ready[0] + '/assessment'
    for headers in ({}, HEADERS | {'Origin': 'https://evil.test'},
                    HEADERS | {'X-Local-Runtime-Token': 'bad'}, HEADERS | {'Host': 'evil.test'},
                    {key: value for key, value in HEADERS.items() if key != 'Origin'}):
        check(local_client.post(endpoint, headers=headers, json=BODY), 403)
    check(local_client.post(endpoint, headers=HEADERS, content='PRIVATE'), 415)
    monkeypatch.setattr(settings, 'app_mode', 'account')
    check(assess(local_client, ready[0], BODY), 403, 'local_mode_required')


@pytest.mark.parametrize('failure', ['host', 'file', 'database', 'protocol', 'eligible'])
def test_unknown_is_read_failure_never_denial(local_client, ready, engine, monkeypatch, failure):
    path, file = ready
    body = issued(local_client, path)
    before = snapshot(engine, file)
    calls = []
    def fail(**kwargs):
        calls.append(1)
        raise OSError('PRIVATE storage failure')
    host = main.app.state.project_write_grants
    if failure == 'host':
        monkeypatch.delattr(main.app.state, 'project_write_grants')
    elif failure == 'file':
        monkeypatch.setattr(host._reader, 'read', fail)
    elif failure == 'database':
        monkeypatch.setattr(service, '_authorize', lambda *a: fail())
    else:
        monkeypatch.setattr(host, 'assess', lambda **kw: (
            ProjectWriteDecision('eligible') if failure == 'eligible' else {'code': 'PRIVATE'}
        ))
    check(assess(local_client, path, body), 500, 'project_write_assessment_read_failed')
    assert calls == ([1] if failure in ('file', 'database') else [])
    assert snapshot(engine, file) == before


def test_openapi_deny_only_contract(local_client):
    schema = local_client.get('/openapi.json', headers=HEADERS).json()
    models = schema['components']['schemas']
    request = models['ProjectWriteAssessmentRequest']
    response = models['ProjectWriteAssessmentResponse']
    assert set(request['required']) == {'grant_id', 'revision', 'apply_requested'}
    assert not request['additionalProperties'] and not response['additionalProperties']
    assert set(response['properties']) == {'workspace_id', 'task_id', 'proposal_id', 'result'}
    assert 'eligible' not in response['properties']['result']['enum']
    assert 'exclusive_access_unconfirmed' in response['properties']['result']['enum']
