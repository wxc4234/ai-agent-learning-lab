"""恢复和审计HTTP的真实授权与严格请求边界。"""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal
from app.services.workspace.proposals import proposal_recovery as recovery
from tests.local.test_local_mode import HEADERS
from tests.workspace.proposals.test_project_write_grant_api import local_client, ready, saved, check

__all__ = ['local_client', 'ready', 'saved']


@pytest.fixture(autouse=True)
def recovery_database(engine, monkeypatch):
    monkeypatch.setattr(recovery, 'SessionLocal', sessionmaker(bind=engine))


@pytest.mark.parametrize('body', [{}, {'action': 'grant'}, {'action': True}, {'action': 'restore', 'path': 'PRIVATE'}, []])
def test_restore_strict_input(local_client, ready, body):
    check(local_client.post(ready[0] + '/restore-proposal', headers=HEADERS, json=body), 422)


@pytest.mark.parametrize('suffix,method', [('audit', 'get'), ('restore-proposal', 'post')])
def test_query_and_missing_identity_rejected(local_client, ready, suffix, method):
    kwargs = {'json': {'action': 'restore'}} if method == 'post' else {}
    send = getattr(local_client, method)
    check(send(ready[0] + '/' + suffix + '?path=PRIVATE', headers=HEADERS, **kwargs), 422)
    check(send(ready[0].replace(ready[0].split('/')[6], 'f' * 32) + '/' + suffix, headers=HEADERS, **kwargs), 404)


def test_restore_origin_and_json_required(local_client, ready):
    path = ready[0] + '/restore-proposal'
    check(local_client.post(path, headers=HEADERS | {'Origin': 'http://untrusted.test'}, json={'action': 'restore'}), 403)
    check(local_client.post(path, headers=HEADERS | {'Content-Type': 'text/plain'}, content='{}'), 415)


def test_restore_pending_conflict_and_audit_projection(local_client, ready):
    path, file = ready
    before = file.read_bytes()
    check(local_client.post(path + '/restore-proposal', headers=HEADERS, json={'action': 'restore'}), 409)
    response = local_client.get(path + '/audit', headers=HEADERS)
    check(response, 200)
    assert set(response.json()) == {'workspace_id', 'task_id', 'proposal_id', 'events'}
    assert str(file.parent) not in response.text
    assert all(set(event) == {'event', 'created_at', 'restore_proposal_id'} for event in response.json()['events'])
    assert file.read_bytes() == before


def test_restore_confirmed_application_creates_pending_once(local_client, ready, engine):
    path, file = ready
    with Session(engine) as session, session.begin():
        row = session.scalar(select(FileEditProposal))
        assert row is not None
        row.baseline_content = file.read_bytes().decode()
        file.write_bytes(row.proposed_content.encode())
        row.application_status = 'applied'
        row.application_token = 'a' * 32
    response = local_client.post(path + '/restore-proposal', headers=HEADERS, json={'action': 'restore'})
    check(response, 200)
    repeat = local_client.post(path + '/restore-proposal', headers=HEADERS, json={'action': 'restore'})
    check(repeat, 200)
    assert repeat.json() == response.json()
    with Session(engine) as session:
        reverse = session.scalar(select(FileEditProposal).where(FileEditProposal.external_id == response.json()['restore_proposal_id']))
        assert reverse is not None and reverse.status == 'pending' and reverse.application_status == 'idle'
