"""隔离PostgreSQL验证授权、绑定提交与文件清理三个独立边界。"""

from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.repositories.workspace.proposal_application_guard import ProposalApplicationBusyError
from app.config import settings
from app.models import Conversation, FileEditProposal, Workspace
from app.services.workspace.samples import task_sample_binding as m
from app.services.workspace.samples import temporary_proposal_sample as lifecycle
from tests.tasks.test_task_deletion_service import target

__all__ = ['target']


@pytest.fixture
def setup(engine, target, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(lifecycle.tempfile, 'gettempdir', lambda: str(tmp_path))
    sessions = []
    class Tracked(Session):
        closed = False
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)
        def close(self):
            super().close()
            self.closed = True
    monkeypatch.setattr(m, 'SessionLocal', sessionmaker(bind=engine, class_=Tracked))
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = None
    service = m.TaskSampleBindings()
    scope = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    yield service, scope, Tracked, sessions
    assert all(s.closed and not s.in_transaction() for s in sessions)
    # 测试结束后可信清理自有样例；生产路径不会自动恢复未知提交。
    for binding in list(service._bindings.values()):
        try:
            service._registry.close(binding.handle)
        except ValueError:
            continue


def root(engine):
    with Session(engine) as session:
        return session.scalar(select(Workspace.root_path))


def test_real_binding_borrow_reauthorize_and_close(setup, engine, tmp_path):
    service, scope, _, sessions = setup
    service.bind(**scope)
    path = Path(root(engine))
    assert path.parent == tmp_path and path.is_dir()
    with service.borrow(**scope) as sample:
        assert sample.root == path
        assert all(s.closed and not s.in_transaction() for s in sessions)
        with pytest.raises(m.TaskSampleBindingError):
            service.close(**scope)
        with pytest.raises(m.TaskSampleBindingError), service.borrow(**scope):
            pytest.fail('exclusive')
    service.close(**scope)
    assert root(engine) is None and not path.exists()
    with pytest.raises(m.TaskSampleBindingError), service.borrow(**scope):
        pytest.fail('closed')


@pytest.mark.parametrize('kind', ['foreign', 'wrong_task', 'conversation', 'already_bound'])
def test_initial_authorization_before_creation(setup, engine, target, tmp_path, kind):
    service, scope, _, _ = setup
    scope = dict(scope)
    if kind == 'foreign':
        scope['user_id'] = target['other_id']
    elif kind == 'wrong_task':
        scope['task_id'] = 'f' * 32
    else:
        with Session(engine) as session, session.begin():
            if kind == 'conversation':
                session.get(Conversation, target['conversation_pk']).user_id = target['other_id']
            else:
                session.scalar(select(Workspace)).root_path = '/existing'
    with pytest.raises((m.TaskSampleBindingError, WorkspaceNotAccessibleError)):
        service.bind(**scope)
    assert list(tmp_path.iterdir()) == [] and service._bindings == {}


@pytest.mark.parametrize('change', ['owner', 'conversation', 'binding'])
def test_borrow_rechecks_database_and_preserves_directory(setup, engine, target, change):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    with Session(engine) as session, session.begin():
        workspace = session.scalar(select(Workspace))
        if change == 'owner':
            workspace.user_id = target['other_id']
        elif change == 'conversation':
            session.get(Conversation, target['conversation_pk']).user_id = target['other_id']
        else:
            workspace.root_path = '/different'
    with pytest.raises((m.TaskSampleBindingError, WorkspaceNotAccessibleError)), service.borrow(**scope):
        pytest.fail('changed')
    assert path.exists()
    with pytest.raises(m.TaskSampleBindingError), service.borrow(**scope):
        pytest.fail('sealed')


def test_other_scope_cannot_borrow_or_close(setup, target, engine):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    for changed in ({**scope, 'user_id': target['other_id']}, {**scope, 'task_id': 'd' * 32}):
        with pytest.raises(m.TaskSampleBindingError), service.borrow(**changed):
            pytest.fail('wrong scope')
        with pytest.raises(m.TaskSampleBindingError):
            service.close(**changed)
    assert path.exists()
    service.close(**scope)


@pytest.mark.parametrize('operation', ['bind', 'close'])
@pytest.mark.parametrize('timing', ['before_commit', 'after_commit'])
def test_uncertain_commit_retains_directory_and_seals(setup, engine, tmp_path, operation, timing):
    service, scope, tracked, _ = setup
    if operation == 'close':
        service.bind(**scope)
    def fail(session):
        raise RuntimeError('commit confirmation lost')
    event.listen(tracked, timing, fail)
    try:
        with pytest.raises(RuntimeError):
            getattr(service, operation)(**scope)
    finally:
        event.remove(tracked, timing, fail)
    directories = list(tmp_path.iterdir())
    assert len(directories) == 1 and directories[0].is_dir()
    expected_bound = (operation == 'bind' and timing == 'after_commit') or (operation == 'close' and timing == 'before_commit')
    assert (root(engine) is not None) is expected_bound
    with pytest.raises(m.TaskSampleBindingError), service.borrow(**scope):
        pytest.fail('unknown commit')
    with pytest.raises(m.TaskSampleBindingError):
        service.close(**scope)


def test_precommit_flush_failure_releases_unpublished_sample(setup, engine, tmp_path):
    service, scope, tracked, _ = setup
    def fail(*args):
        raise RuntimeError('flush failed')
    event.listen(tracked, 'before_flush', fail)
    try:
        with pytest.raises(RuntimeError):
            service.bind(**scope)
    finally:
        event.remove(tracked, 'before_flush', fail)
    assert root(engine) is None and list(tmp_path.iterdir()) == []
    assert service._bindings == {}


def test_second_authorization_rejects_concurrent_binding(setup, engine, tmp_path, monkeypatch):
    service, scope, _, _ = setup
    create = service._registry.create
    def changed():
        handle = create()
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = '/external'
        return handle
    monkeypatch.setattr(service._registry, 'create', changed)
    with pytest.raises(m.TaskSampleBindingError):
        service.bind(**scope)
    assert root(engine) == '/external' and list(tmp_path.iterdir()) == []


def test_borrow_interruption_keeps_bound_directory(setup, engine):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught, service.borrow(**scope):
        raise error
    assert caught.value is error and path.exists() and root(engine) == str(path)
    with pytest.raises(m.TaskSampleBindingError), service.borrow(**scope):
        pytest.fail('failed use')


def test_close_cleanup_failure_leaves_unbound_and_invalid(setup, engine):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    (path / 'unknown').write_bytes(b'keep')
    with pytest.raises(lifecycle.TemporarySampleError):
        service.close(**scope)
    assert root(engine) is None and path.exists()
    with pytest.raises(m.TaskSampleBindingError), service.borrow(**scope):
        pytest.fail('cleanup failed')


def test_account_mode_has_no_side_effects(setup, tmp_path, monkeypatch):
    service, scope, _, _ = setup
    monkeypatch.setattr(settings, 'app_mode', 'account')
    with pytest.raises(m.TaskSampleBindingError):
        service.bind(**scope)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('status', ['running', 'uncertain'])
@pytest.mark.parametrize('operation', ['bind', 'close'])
def test_active_application_blocks_binding_changes(setup, engine, target, tmp_path, status, operation):
    service, scope, _, _ = setup
    if operation == 'close':
        service.bind(**scope)
    before = root(engine)
    with Session(engine) as session, session.begin():
        session.add(FileEditProposal(
            external_id='f' * 32, task_id=target['task_pk'],
            bound_root=before or '/historical', relative_path='example.txt',
            baseline_sha256='a' * 64, proposed_sha256='b' * 64,
            proposed_content='new', diff='diff', diff_truncated=False,
            status='approved', application_status=status, application_token='c' * 32,
        ))
    with pytest.raises(ProposalApplicationBusyError):
        getattr(service, operation)(**scope)
    assert root(engine) == before
    if before:
        assert Path(before).exists()
    else:
        assert list(tmp_path.iterdir()) == []


def test_duplicate_bind_and_new_instance_cannot_adopt_directory(setup, engine):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = root(engine)
    with pytest.raises(m.TaskSampleBindingError):
        service.bind(**scope)
    fresh = m.TaskSampleBindings()
    with pytest.raises(m.TaskSampleBindingError):
        fresh.bind(**scope)
    with pytest.raises(m.TaskSampleBindingError), fresh.borrow(**scope):
        pytest.fail('not registered here')
    assert root(engine) == path
    service.close(**scope)
