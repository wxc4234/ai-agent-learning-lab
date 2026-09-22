"""真实隔离PostgreSQL验证登记查询授权、纯读取及生命周期快照。"""

from dataclasses import FrozenInstanceError, asdict
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.samples import task_sample_binding as module
from tests.workspace.samples.test_task_sample_binding import setup, target

__all__ = ['setup', 'target']


def test_missing_ready_busy_and_closed(setup):
    service, scope, _, sessions = setup
    assert service.read_status(**scope).status == 'missing'
    service.bind(**scope)
    snapshot = service.read_status(**scope)
    assert asdict(snapshot) == {'status': 'ready'}
    with pytest.raises(FrozenInstanceError):
        snapshot.status = 'missing'
    # 在另一线程查询，验证busy是活动借用状态，而非同线程锁重入假象。
    with service.borrow(**scope), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(service.read_status, **scope).result(timeout=3).status == 'busy'
    assert service.read_status(**scope).status == 'ready'
    assert all(s.closed and not s.in_transaction() for s in sessions)
    service.close(**scope)
    assert service.read_status(**scope).status == 'missing'
    assert snapshot.status == 'ready'


@pytest.mark.parametrize('registered', [False, True])
@pytest.mark.parametrize('change', ['owner', 'task', 'workspace', 'conversation'])
def test_authorizes_even_when_registration_missing(setup, target, engine, registered, change):
    service, scope, _, _ = setup
    if registered:
        service.bind(**scope)
    args = dict(scope)
    if change == 'owner':
        args['user_id'] = target['other_id']
    elif change == 'task':
        args['task_id'] = 'f' * 32
    elif change == 'workspace':
        args['workspace_id'] = 'f' * 32
    else:
        with Session(engine) as session, session.begin():
            session.get(Conversation, target['conversation_pk']).user_id = target['other_id']
    with pytest.raises(WorkspaceNotAccessibleError):
        service.read_status(**args)


@pytest.mark.parametrize('state', ['uncertain', 'unexpected', 'preparing'])
def test_internal_states_never_expose_private_fields(setup, state):
    service, scope, _, _ = setup
    service.bind(**scope)
    binding = next(iter(service._bindings.values()))
    binding.state = state
    before = (binding.state, binding.busy, binding.handle, binding.root)
    assert asdict(service.read_status(**scope)) == {'status': 'busy' if state == 'preparing' else 'sealed'}
    assert (binding.state, binding.busy, binding.handle, binding.root) == before


def test_root_mismatch_reports_sealed_without_mutating_binding(setup, engine):
    service, scope, _, _ = setup
    service.bind(**scope)
    binding = next(iter(service._bindings.values()))
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = '/changed'
    assert service.read_status(**scope).status == 'sealed'
    assert binding.state == 'ready'
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) == '/changed'


def test_no_registry_operations_or_database_commit(setup, monkeypatch):
    service, scope, tracked, _ = setup
    service.bind(**scope)
    def forbidden(*args, **kwargs):
        pytest.fail('read-only query must not create, borrow, close or commit')
    # 测试退出前恢复registry方法，让共用夹具正常清理自有文件。
    with monkeypatch.context() as patch:
        for name in ('create', 'borrow', 'close'):
            patch.setattr(service._registry, name, forbidden)
        patch.setattr(tracked, 'commit', forbidden)
        assert service.read_status(**scope).status == 'ready'
        assert module.TaskSampleBindings().read_status(**scope).status == 'missing'


@pytest.mark.parametrize('kind', ['mode', 'pid'])
def test_process_boundary_precedes_lock_and_database(setup, monkeypatch, kind):
    service, scope, _, _ = setup
    if kind == 'mode':
        monkeypatch.setattr(settings, 'app_mode', 'account')
    else:
        service._pid -= 1
    monkeypatch.setattr(module, 'SessionLocal', lambda: pytest.fail('must reject before database'))
    with pytest.raises(module.TaskSampleBindingError):
        service.read_status(**scope)


def test_database_failure_is_not_missing(setup, monkeypatch):
    service, scope, _, _ = setup
    def fail():
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(module, 'SessionLocal', fail)
    with pytest.raises(RuntimeError, match='database unavailable'):
        service.read_status(**scope)


def test_borrow_failure_stays_sealed_after_query(setup):
    service, scope, _, _ = setup
    service.bind(**scope)
    with pytest.raises(RuntimeError), service.borrow(**scope):
        raise RuntimeError('execution outcome unknown')
    assert service.read_status(**scope).status == 'sealed'
    with pytest.raises(module.TaskSampleBindingError), service.borrow(**scope):
        pytest.fail('query must not restore access')
