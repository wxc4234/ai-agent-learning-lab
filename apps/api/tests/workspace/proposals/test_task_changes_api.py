"""任务改动列表：隔离PostgreSQL授权、分页与状态公开边界。"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import FileEditProposal, Workspace, User
from app.services.workspace.proposals import file_edit_proposal_list as service
from tests.workspace.proposals import test_file_edit_proposal_api as existing
from tests.local.test_local_mode import HEADERS

local_client = existing.local_client
saved = existing.saved


@pytest.fixture
def query(saved, engine, monkeypatch):
    monkeypatch.setattr(service, 'SessionLocal', sessionmaker(bind=engine))
    return saved[0].rsplit('/', 1)[0]


def test_public_list_and_keyset_pagination(local_client, query, saved, engine):
    with Session(engine) as session, session.begin():
        original = session.scalar(select(FileEditProposal))
        for index in range(51):
            session.add(FileEditProposal(
                external_id=uuid4().hex, task_id=original.task_id, bound_root='PRIVATE',
                relative_path=f'file-{index}.txt', baseline_sha256='a'*64, proposed_sha256='b'*64,
                proposed_content='PRIVATE', diff='PRIVATE', diff_truncated=False,
                status='approved', application_status='applied', application_token=uuid4().hex,
            ))
    response = local_client.get(query, headers=HEADERS)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    first = response.json()
    assert len(first['items']) == 50 and first['next_cursor']
    assert 'PRIVATE' not in response.text and 'application_token' not in response.text
    assert all(item['application_status'] == 'applied' for item in first['items'])
    second = local_client.get(query, params={'before': first['next_cursor']}, headers=HEADERS).json()
    assert len(second['items']) == 2 and second['next_cursor'] is None
    assert not ({item['proposal_id'] for item in first['items']} & {item['proposal_id'] for item in second['items']})
    assert second['items'][-1]['status'] == 'pending' and second['items'][-1]['application_status'] == 'idle'
    assert saved[1].read_bytes() == b'old\r\n'


@pytest.mark.parametrize('kind', ['workspace', 'task', 'owner'])
def test_wrong_scope_is_not_empty_success(local_client, query, engine, kind):
    if kind == 'workspace':
        query = query.replace(query.split('/')[2], '0'*32)
    elif kind == 'task':
        query = query.replace(query.split('/')[4], '0'*32)
    else:
        with Session(engine) as session, session.begin():
            other = User(external_id=uuid4().hex)
            session.add(other)
            session.flush()
            session.scalar(select(Workspace)).user_id = other.id
    assert local_client.get(query, headers=HEADERS).status_code == 404


@pytest.mark.parametrize('before', ['0', '-1', 'abc', '2147483648'])
def test_invalid_cursor(local_client, query, before):
    assert local_client.get(query, params={'before': before}, headers=HEADERS).status_code == 422


def test_database_failure_is_not_empty_success(local_client, query, monkeypatch):
    def fail():
        raise RuntimeError('PRIVATE')
    monkeypatch.setattr(service, 'SessionLocal', fail)
    response = local_client.get(query, headers=HEADERS)
    assert response.status_code == 500 and 'PRIVATE' not in response.text
