"""隔离PostgreSQL、真实自建Git及并发借用门禁。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import task_git_samples as service
from app.services.workspace.git.status_capture import GitStatusCaptureError
from tests.tasks.test_task_deletion_service import target

__all__ = ['target']


@pytest.fixture
def setup(engine, target, monkeypatch):
    sessions = []
    class Tracked(Session):
        closed = False
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)
        def close(self):
            super().close()
            self.closed = True
    monkeypatch.setattr(service, 'SessionLocal', sessionmaker(bind=engine, class_=Tracked))
    manager = service.TaskGitSamples()
    scope = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    yield manager, scope, sessions
    manager.shutdown()
    assert all(session.closed and not session.in_transaction() for session in sessions)


def sample(manager, scope):
    # 仅测试读取私有句柄准备自有样例，业务返回值从不暴露目录。
    return manager._bindings[tuple(scope.values())].sample


def test_real_scope_query_no_directory_binding_or_file_writes(setup, engine, monkeypatch):
    manager, scope, sessions = setup
    manager.bind(**scope)
    root = sample(manager, scope).root
    file = root / '中文 file.txt'
    file.write_text('content')
    before = (file.read_bytes(), file.stat().st_ino, file.stat().st_mtime_ns)
    original = service.collect_sample_git_status
    def collect(handle):
        assert all(session.closed and not session.in_transaction() for session in sessions)
        return original(handle)
    monkeypatch.setattr(service, 'collect_sample_git_status', collect)
    result = manager.read_status(**scope)
    assert [(entry.xy, entry.path) for entry in result.entries] == [('??', '中文 file.txt')]
    assert (file.read_bytes(), file.stat().st_ino, file.stat().st_mtime_ns) == before
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) == '/preserved'
    manager.close(**scope)
    assert not root.exists()
    with pytest.raises(service.TaskGitSampleError):
        manager.read_status(**scope)


@pytest.mark.parametrize('kind', ['foreign', 'sibling', 'workspace', 'missing'])
def test_wrong_scope_cannot_read_or_close(setup, target, monkeypatch, kind):
    manager, scope, _ = setup
    manager.bind(**scope)
    wrong = dict(scope)
    wrong[{'foreign': 'user_id', 'sibling': 'task_id', 'workspace': 'workspace_id', 'missing': 'task_id'}[kind]] = target['other_id'] if kind == 'foreign' else 'd' * 32 if kind == 'sibling' else 'f' * 32
    monkeypatch.setattr(service, 'collect_sample_git_status', lambda handle: pytest.fail('must not execute'))
    for method in (manager.read_status, manager.close):
        with pytest.raises(service.TaskGitSampleError):
            method(**wrong)
    assert sample(manager, scope).root.exists()


@pytest.mark.parametrize('kind', ['workspace-owner', 'conversation-owner', 'conversation-deleted'])
def test_reauthorize_on_every_query(setup, engine, target, monkeypatch, kind):
    manager, scope, _ = setup
    manager.bind(**scope)
    with Session(engine) as session, session.begin():
        if kind == 'workspace-owner':
            session.scalar(select(Workspace)).user_id = target['other_id']
        elif kind == 'conversation-owner':
            session.get(Conversation, target['conversation_pk']).user_id = target['other_id']
        else:
            session.delete(session.get(Conversation, target['conversation_pk']))
    monkeypatch.setattr(service, 'collect_sample_git_status', lambda handle: pytest.fail('must not execute'))
    with pytest.raises(WorkspaceNotAccessibleError):
        manager.read_status(**scope)
    root = sample(manager, scope).root
    manager.shutdown()  # 可信宿主仍能清理自己的句柄，不依赖已丢失的资源归属。
    assert not root.exists()


def test_foreign_bind_never_creates_sample(setup, target, monkeypatch):
    manager, scope, _ = setup
    monkeypatch.setattr(service, 'temporary_git_status_sample', lambda: pytest.fail('must not create'))
    with pytest.raises(WorkspaceNotAccessibleError):
        manager.bind(**{**scope, 'user_id': target['other_id']})


def test_creation_race_cleans_owned_sample(setup, engine, target, monkeypatch):
    manager, scope, _ = setup
    factory = service.temporary_git_status_sample
    roots = []
    @contextmanager
    def create():
        with factory() as handle:
            roots.append(handle.root)
            with Session(engine) as session, session.begin():
                session.get(Conversation, target['conversation_pk']).user_id = target['other_id']
            yield handle
    monkeypatch.setattr(service, 'temporary_git_status_sample', create)
    with pytest.raises(WorkspaceNotAccessibleError):
        manager.bind(**scope)
    assert not manager._bindings and not roots[0].exists()


def test_busy_query_blocks_close_shutdown_and_second_query(setup, monkeypatch):
    manager, scope, _ = setup
    manager.bind(**scope)
    entered, release = Event(), Event()
    original = service.collect_sample_git_status
    def held(handle):
        entered.set()
        assert release.wait(5)
        return original(handle)
    monkeypatch.setattr(service, 'collect_sample_git_status', held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.read_status, **scope)
        try:
            assert entered.wait(5)
            for method in (manager.close, manager.read_status, manager.bind):
                with pytest.raises(service.TaskGitSampleError):
                    method(**scope)
            with pytest.raises(service.TaskGitSampleError):
                manager.shutdown()
        finally:
            release.set()
        assert future.result(timeout=5).entries == ()
    root = sample(manager, scope).root
    manager.close(**scope)
    assert not root.exists()


@pytest.mark.parametrize('error', [GitStatusCaptureError('git_status_timeout'), KeyboardInterrupt()])
def test_failure_propagates_without_retry_and_releases_borrow(setup, monkeypatch, error):
    manager, scope, _ = setup
    manager.bind(**scope)
    calls = []
    def fail(handle):
        calls.append(handle)
        raise error
    monkeypatch.setattr(service, 'collect_sample_git_status', fail)
    with pytest.raises(type(error)) as caught:
        manager.read_status(**scope)
    assert caught.value is error and len(calls) == 1
    root = sample(manager, scope).root
    manager.close(**scope)
    assert not root.exists()


def test_no_restore_in_new_manager_or_after_shutdown(setup):
    manager, scope, _ = setup
    manager.bind(**scope)
    with pytest.raises(service.TaskGitSampleError):
        service.TaskGitSamples().read_status(**scope)
    manager.shutdown()
    for method in (manager.bind, manager.read_status):
        with pytest.raises(service.TaskGitSampleError):
            method(**scope)


def test_recreated_conversation_is_not_old_registration(setup, engine, target):
    manager, scope, _ = setup
    manager.bind(**scope)
    with Session(engine) as session, session.begin():
        session.delete(session.get(Conversation, target['conversation_pk']))
        session.flush()
        session.add(Conversation(external_id=target['conversation_id'], user_id=target['user_id'], task_id=target['task_pk']))
    with pytest.raises(service.TaskGitSampleError):
        manager.read_status(**scope)


def test_expired_underlying_handle_does_not_fall_back(setup):
    manager, scope, _ = setup
    manager.bind(**scope)
    binding = manager._bindings[tuple(scope.values())]
    binding.lifetime.__exit__(None, None, None)
    with pytest.raises(GitStatusCaptureError) as caught:
        manager.read_status(**scope)
    assert caught.value.code == 'git_sample_unavailable'


def test_cleanup_failure_seals_registration(setup):
    manager, scope, _ = setup
    manager.bind(**scope)
    binding = manager._bindings[tuple(scope.values())]
    lifetime = binding.lifetime
    class FailedCleanup:
        def __exit__(self, *args):
            raise OSError('injected cleanup failure')
    binding.lifetime = FailedCleanup()
    try:
        with pytest.raises(OSError):
            manager.close(**scope)
        for method in (manager.read_status, manager.close):
            with pytest.raises(service.TaskGitSampleError):
                method(**scope)
        with pytest.raises(service.TaskGitSampleError):
            manager.shutdown()
        assert binding.sample.root.exists()
    finally:
        # 仅撤销测试注入以清理自有目录，生产代码没有解除封锁入口。
        binding.lifetime = lifetime
        binding.sealed = False
