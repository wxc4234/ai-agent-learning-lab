"""创建幂等 HTTP 契约：真实路由与隔离 PostgreSQL，故障仅在明确边界注入。"""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import Conversation, Task, TaskCreationRequest, User, Workspace
from app.routers.workspace import workspace
from app.services.tasks import task_service
import tests.tasks.test_task_api as task_api
from tests.tasks.test_task_creation_idempotency import KEY, counts
from tests.local.test_local_mode import HEADERS
from tests.workspace.test_workspace_binding_api import safe

local_client = task_api.local_client
target = task_api.target


def post(client, target, **changes):
    return task_api.post(client, target, {'title': '学习 Agent', 'request_key': KEY} | changes)


def test_real_replay_uses_same_identity_and_current_public_title(local_client, target, engine):
    first = post(local_client, target, title=' \t学习 Agent\n')
    safe(first, 201)
    again = post(local_client, target)
    safe(again, 201)
    assert again.json() == first.json()
    assert set(again.json()) == {'external_id', 'workspace_id', 'conversation_id', 'title', 'created_at'}
    with engine.begin() as conn:
        conn.execute(update(Task).values(title='总结后的标题'))
    current = post(local_client, target)
    safe(current, 201)
    assert current.json() == first.json() | {'title': '总结后的标题'}
    assert counts(engine) == (1, 1, 1)
    with Session(engine) as reader:
        receipt = reader.scalar(select(TaskCreationRequest))
        task = reader.get(Task, receipt.task_id)
        assert task.external_id == first.json()['external_id']
        assert task.workspace.external_id == target[0]
        assert task.workspace.user_id == receipt.user_id == task.conversation.user_id


def test_conflict_then_original_replay(local_client, target, engine):
    first = post(local_client, target)
    safe(first, 201)
    safe(post(local_client, target, title='PRIVATE changed'), 409, 'task_creation_conflict')
    assert post(local_client, target).json() == first.json()
    assert counts(engine) == (1, 1, 1)


def test_real_delete_preserves_key_and_new_key_can_create(local_client, target, engine):
    first = post(local_client, target)
    safe(first, 201)
    deleted = local_client.delete(
        f"/workspaces/{target[0]}/tasks/{first.json()['external_id']}", headers=HEADERS,
    )
    safe(deleted, 204)
    safe(post(local_client, target), 409, 'task_creation_result_deleted')
    safe(post(local_client, target, title='PRIVATE changed'), 409, 'task_creation_conflict')
    assert counts(engine) == (0, 0, 1)
    with Session(engine) as reader:
        assert reader.scalar(select(TaskCreationRequest)).task_id is None
    fresh = post(local_client, target, request_key='f' * 32)
    safe(fresh, 201)
    assert fresh.json()['external_id'] != first.json()['external_id']
    assert counts(engine) == (1, 1, 2)


@pytest.mark.parametrize('explicit_null', [False, True])
def test_legacy_missing_or_null_key_remains_nonidempotent(local_client, target, engine, explicit_null):
    body = {'title': '任务'}
    if explicit_null:
        body['request_key'] = None
    first = task_api.post(local_client, target, body)
    second = task_api.post(local_client, target, body)
    safe(first, 201)
    safe(second, 201)
    assert first.json()['external_id'] != second.json()['external_id']
    assert counts(engine) == (2, 2, 0)


@pytest.mark.parametrize('key', ['', 'a' * 31, 'a' * 33, 'A' * 32, 'g' * 32,
                                 ' ' + KEY, KEY + '\n', 42, True, [], {}])
def test_bad_key_is_safe_422_before_service(local_client, target, engine, monkeypatch, key):
    def forbidden(**kwargs):
        pytest.fail('非法正文不能进入创建事务')
    monkeypatch.setattr(workspace, 'create_workspace_task', forbidden)
    safe(post(local_client, target, request_key=key), 422, 'invalid_task_input')
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize('field', ['request_hash', 'user_id', 'task_id', 'conversation_id'])
def test_client_cannot_supply_trusted_fields(local_client, target, engine, field):
    safe(post(local_client, target, **{field: 'PRIVATE'}), 422, 'invalid_task_input')
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize('error,status', [
    (task_service.InvalidTaskRequestKeyError, 422),
    (task_service.TaskCreationConflictError, 409),
    (task_service.TaskCreationResultDeletedError, 409),
])
def test_service_errors_map_without_reflecting_exception(local_client, target, engine, monkeypatch, error, status):
    def fail(**kwargs):
        assert kwargs['request_key'] == KEY
        exc = error()
        exc.args = ('PRIVATE SQL credential',)
        raise exc
    monkeypatch.setattr(workspace, 'create_workspace_task', fail)
    safe(post(local_client, target), status, error.code)
    assert counts(engine) == (0, 0, 0)


@pytest.mark.parametrize('kind', ['missing', 'other-owner'])
def test_key_does_not_bypass_project_ownership(local_client, target, engine, kind):
    safe(post(local_client, target), 201)
    identifier = uuid4().hex
    if kind == 'other-owner':
        with Session(engine) as session, session.begin():
            owner = User(external_id='PRIVATE other-owner')
            session.add(Workspace(external_id=identifier, name='PRIVATE', user=owner))
    safe(post(local_client, (identifier, target[1])), 404, 'workspace_not_accessible')
    assert counts(engine) == (1, 1, 1)


def test_same_key_in_different_owned_project_is_independent(local_client, target, engine):
    another = local_client.post('/workspaces', headers=HEADERS, json={'name': '第二项目'})
    safe(another, 201)
    first = post(local_client, target)
    second = post(local_client, (another.json()['external_id'], target[1]), title='不同内容')
    safe(first, 201)
    safe(second, 201)
    assert first.json()['external_id'] != second.json()['external_id']
    assert counts(engine) == (2, 2, 2)


@pytest.mark.parametrize('headers,code', [
    ({}, 'local_access_rejected'),
    (HEADERS | {'X-Local-Runtime-Token': 'PRIVATE'}, 'local_access_rejected'),
    (HEADERS | {'Host': 'evil.test'}, 'local_access_rejected'),
    (HEADERS | {'Origin': 'https://evil.test'}, 'local_access_rejected'),
    ({'X-Local-Runtime-Token': 'a' * 64}, 'workspace_origin_rejected'),
])
def test_key_cannot_bypass_local_boundary(local_client, target, engine, monkeypatch, headers, code):
    def forbidden():
        pytest.fail('边界拒绝后不能查询身份')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    response = task_api.post(local_client, target, {'title': '任务', 'request_key': KEY}, headers)
    safe(response, 403, code)
    assert counts(engine) == (0, 0, 0)


def test_nonlocal_rejected_before_identity(local_client, target, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'account')
    def forbidden():
        pytest.fail('非本地模式不能查询身份')
    monkeypatch.setattr(dependencies, 'SessionLocal', forbidden)
    safe(post(local_client, target), 403, 'local_mode_required')


@pytest.mark.parametrize('failure', ['response', 'invalid-result', 'serialization', 'after-commit'])
def test_committed_but_failed_response_then_same_key_replays(local_client, target, engine, monkeypatch, failure):
    original = workspace.create_workspace_task
    with monkeypatch.context() as patch:
        if failure in {'invalid-result', 'after-commit'}:
            def broken_service(**kwargs):
                result = original(**kwargs)
                if failure == 'after-commit':
                    raise RuntimeError('PRIVATE confirmation lost')
                return replace(result, external_id='PRIVATE')
            patch.setattr(workspace, 'create_workspace_task', broken_service)
        else:
            def broken_response(**kwargs):
                if failure == 'serialization':
                    # 由实际注册的 FastAPI response_model 拒绝畸形结果。
                    return kwargs | {'external_id': 'PRIVATE'}
                raise RuntimeError('PRIVATE response construction')
            patch.setattr(workspace, 'TaskResponse', broken_response)
        safe(post(local_client, target), 500, 'task_creation_uncertain')
    assert counts(engine) == (1, 1, 1)
    with Session(engine) as reader:
        task = reader.scalar(select(Task))
        original_id = task.external_id
        original_conversation = task.conversation.external_id
    retry = post(local_client, target)
    safe(retry, 201)
    assert retry.json()['external_id'] == original_id
    assert retry.json()['conversation_id'] == original_conversation
    assert counts(engine) == (1, 1, 1)


def test_real_sql_failure_rolls_back_then_same_key_creates(local_client, target, engine):
    def fail(mapper, connection, receipt):
        # 两条业务记录已写入但未提交；第三条记录失败须整体回滚。
        assert connection.scalar(select(Task.id)) is not None
        assert connection.scalar(select(Conversation.id)) is not None
        connection.execute(text('SELECT * FROM missing_http_idempotency_table'))
    event.listen(TaskCreationRequest, 'before_insert', fail)
    try:
        safe(post(local_client, target), 500, 'task_creation_uncertain')
    finally:
        event.remove(TaskCreationRequest, 'before_insert', fail)
    assert counts(engine) == (0, 0, 0)
    safe(post(local_client, target), 201)
    assert counts(engine) == (1, 1, 1)
