"""新变更组与隔离副本入口使用真实本地认证和统一来源/JSON门禁。"""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from app.models import Workspace
from app.services.workspace.proposals import change_sets
from app.services.workspace.areas import owned_areas
from tests.local.test_local_mode import HEADERS
from tests.workspace.proposals.test_project_write_grant_api import local_client, ready, saved, check
__all__ = ['local_client', 'ready', 'saved']

@pytest.fixture
def resource(ready, engine, monkeypatch, tmp_path):
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(change_sets, 'SessionLocal', factory)
    monkeypatch.setattr(owned_areas, 'SessionLocal', factory)
    monkeypatch.setattr(owned_areas, 'AREA_BASE', tmp_path / 'managed')
    parts = ready[0].split('/')
    with Session(engine) as session:
        owner = session.scalar(select(Workspace.user_id).where(Workspace.external_id == parts[2]))
    scope = {'user_id': owner, 'workspace_id': parts[2], 'task_id': parts[4]}
    row = change_sets.create_change_set(**scope, operations=[{'kind': 'create', 'path': 'new.txt', 'content': 'hello'}])
    return '/'.join(parts[:5]), row, ready[1].parent


def test_real_decision_apply_restore_api(local_client, resource):
    base, row, root = resource
    url = base + '/change-sets/' + row['change_id']
    response = local_client.get(base + '/change-sets', headers=HEADERS)
    check(response, 200)
    assert len(response.json()['items']) == 1 and str(root) not in response.text
    for action, status in [('approve', 'approved'), ('apply', 'applied'), ('restore', 'rolled_back')]:
        response = local_client.post(url, headers=HEADERS, json={'action': action})
        check(response, 200)
        assert response.json()['item']['status'] == status
    check(local_client.post(url, headers=HEADERS, json={'action': 'apply'}), 409)
    assert not (root / 'new.txt').exists()


@pytest.mark.parametrize('suffix', ['change-sets/fake', 'owned-area'])
@pytest.mark.parametrize('kind', ['origin', 'content_type', 'extra', 'query'])
def test_strict_mutation_boundary(local_client, resource, suffix, kind):
    base, row, _ = resource
    suffix = suffix.replace('fake', row['change_id'])
    headers = HEADERS.copy()
    body = {'action': 'create' if suffix == 'owned-area' else 'approve'}
    if kind == 'origin': headers['Origin'] = 'http://untrusted.test'
    elif kind == 'content_type': headers['Content-Type'] = 'text/plain'
    elif kind == 'extra': body['root_path'] = 'PRIVATE'
    else: suffix += '?user_id=9'
    response = local_client.post(base + '/' + suffix, headers=headers, json=body)
    check(response, {'origin': 403, 'content_type': 415, 'extra': 422, 'query': 422}[kind])
