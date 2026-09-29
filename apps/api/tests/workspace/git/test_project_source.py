"""真实隔离PostgreSQL与临时目录；不启动Git或连接开发业务表。"""

import os
from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.git import project_source as service
from app.services.workspace.git.project_status_plan import ProjectGitStatusPlanError
from tests.tasks.test_task_deletion_service import target
from tests.assertions import require_value

__all__ = ['target']


@pytest.fixture
def setup(engine, target, tmp_path, monkeypatch):
    root = tmp_path.resolve() / 'project'
    root.mkdir()
    (root / 'example.txt').write_text('untouched')
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(Workspace))).root_path = str(root)
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
    reference = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    reference.update(plan_id='project_git_status_v1', binding_revision=1)
    yield reference, root, sessions
    assert all(session.closed and not session.in_transaction() for session in sessions)


def test_success_reads_only_two_selects_outside_filesystem_observation(setup, engine, monkeypatch):
    reference, root, sessions = setup
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    observe = service._observe_root
    @contextmanager
    def checked(path):
        assert all(session.closed for session in sessions)
        with observe(path) as identity:
            yield identity
            assert all(session.closed for session in sessions)
    monkeypatch.setattr(service, '_observe_root', checked)
    before = (root.stat(), (root / 'example.txt').stat(), (root / 'example.txt').read_bytes())
    try:
        result = service.read_project_git_source(reference)
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert len(statements) == 2
    assert all(sql.lstrip().lower().startswith('select') and 'for update' not in sql.lower() for sql in statements)
    assert len(sessions) == 2
    assert result.root_identity == (before[0].st_dev, before[0].st_ino)
    assert result.bound_root == str(root)
    assert str(root) not in repr(result)
    assert result.binding_revision == 1
    pytest.raises(FrozenInstanceError, setattr, result, 'binding_revision', 2)
    assert (root / 'example.txt').read_bytes() == before[2]
    assert (root / 'example.txt').stat().st_mtime_ns == before[1].st_mtime_ns
    assert root.stat().st_mtime_ns == before[0].st_mtime_ns
    # 没有.git也能通过根目录观察；此接口明确不验证Git仓库。
    assert not (root / '.git').exists()


@pytest.mark.parametrize('kind', ['foreign', 'workspace', 'task', 'conversation', 'wrong-project'])
def test_unauthorized_never_opens_directory(setup, target, engine, monkeypatch, kind):
    reference = dict(setup[0])
    if kind == 'foreign':
        reference['user_id'] = target['other_id']
    elif kind in ('workspace', 'task'):
        reference[kind + '_id'] = 'f' * 32
    elif kind == 'wrong-project':
        with Session(engine) as session, session.begin():
            session.add(Workspace(external_id='9' * 32, user_id=target['user_id'], name='another'))
        reference['workspace_id'] = '9' * 32
    else:
        with Session(engine) as session, session.begin():
            require_value(session.get(Conversation, target['conversation_pk'])).user_id = target['other_id']
    monkeypatch.setattr(service, '_observe_root', lambda path: pytest.fail('authorize before filesystem'))
    with pytest.raises(WorkspaceNotAccessibleError):
        service.read_project_git_source(reference)


@pytest.mark.parametrize('kind,code', [('unbound', 'unbound'), ('stale', 'stale')])
def test_binding_gate_precedes_directory_io(setup, engine, monkeypatch, kind, code):
    with Session(engine) as session, session.begin():
        row = require_value(session.scalar(select(Workspace)))
        if kind == 'unbound':
            row.root_path = None
        else:
            row.binding_revision += 1
    monkeypatch.setattr(service, '_observe_root', lambda path: pytest.fail('binding gate first'))
    with pytest.raises(service.ProjectGitSourceError) as caught:
        service.read_project_git_source(setup[0])
    assert caught.value.code == 'project_git_source_' + code


@pytest.mark.parametrize('kind', ['revision', 'path', 'owner', 'conversation'])
def test_second_query_rejects_changes_during_observation(setup, engine, target, monkeypatch, kind):
    observe = service._observe_root
    @contextmanager
    def changed(path):
        with observe(path) as identity:
            with Session(engine) as session, session.begin():
                workspace = require_value(session.scalar(select(Workspace)))
                if kind == 'revision':
                    workspace.binding_revision += 2  # 同路径ABA也必须留下修订差异。
                elif kind == 'path':
                    workspace.root_path = str(setup[1].parent)
                elif kind == 'owner':
                    workspace.user_id = target['other_id']
                else:
                    session.delete(require_value(session.get(Conversation, target['conversation_pk'])))
            yield identity
    monkeypatch.setattr(service, '_observe_root', changed)
    with pytest.raises((service.ProjectGitSourceError, WorkspaceNotAccessibleError)):
        service.read_project_git_source(setup[0])


@pytest.mark.parametrize('kind', ['root', 'ancestor', 'symlink'])
def test_directory_replacement_after_second_query_is_rejected(setup, monkeypatch, kind):
    original = service.read_owned_git_binding
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            directory = setup[1].parent if kind == 'ancestor' else setup[1]
            moved = directory.with_name(directory.name + '-old')
            directory.rename(moved)
            if kind == 'symlink':
                directory.symlink_to(moved, target_is_directory=True)
            else:
                directory.mkdir()
        return result
    monkeypatch.setattr(service, 'read_owned_git_binding', changed)
    with pytest.raises(service.ProjectGitSourceError):
        service.read_project_git_source(setup[0])


@pytest.mark.parametrize('kind', ['missing', 'file', 'symlink', 'ancestor-symlink'])
def test_invalid_directory_never_becomes_source(setup, engine, kind):
    root = setup[1]
    if kind == 'ancestor-symlink':
        alias = root.parent / 'alias'
        alias.symlink_to(root.parent, target_is_directory=True)
        with Session(engine) as session, session.begin():
            require_value(session.scalar(select(Workspace))).root_path = str(alias / root.name)
    else:
        moved = root.with_name('old')
        root.rename(moved)
        if kind == 'file':
            root.write_text('not a directory')
        elif kind == 'symlink':
            root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(service.ProjectGitSourceError):
        service.read_project_git_source(setup[0])


@pytest.mark.parametrize('stage', [1, 2])
def test_database_failure_is_unknown_and_does_not_leak(setup, monkeypatch, stage):
    original = service.read_owned_git_binding
    calls = 0
    def failed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == stage:
            raise RuntimeError('private database secret')
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'read_owned_git_binding', failed)
    with pytest.raises(service.ProjectGitSourceError) as caught:
        service.read_project_git_source(setup[0])
    assert str(caught.value) == 'project_git_source_unavailable'


def test_invalid_reference_does_not_query(monkeypatch):
    monkeypatch.setattr(service, 'SessionLocal', lambda: pytest.fail('must not query'))
    with pytest.raises(ProjectGitStatusPlanError):
        service.read_project_git_source({'root': '/private'})


@pytest.mark.parametrize('outcome', ['success', 'failure', 'interrupt'])
def test_descriptors_closed_after_all_outcomes(tmp_path, monkeypatch, outcome):
    original = os.open
    descriptors = []
    def tracked(*args, **kwargs):
        descriptor = original(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor
    monkeypatch.setattr(os, 'open', tracked)
    monkeypatch.setattr(os, 'supports_dir_fd', {*os.supports_dir_fd, tracked})
    def read():
        with service._observe_root(str(tmp_path.resolve())):
            if outcome == 'failure':
                raise ValueError('failed')
            if outcome == 'interrupt':
                raise KeyboardInterrupt()
    if outcome == 'success':
        read()
    else:
        with pytest.raises(ValueError if outcome == 'failure' else KeyboardInterrupt):
            read()
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize('path', ['/', '//tmp', '/tmp/../other', '/tmp//other', '/tmp/.', 'relative', '/tmp/\x00bad', '/tmp/' + 'x' * 4096])
def test_invalid_path_syntax_rejected(path):
    with pytest.raises(service.ProjectGitSourceError), service._observe_root(path):
        pytest.fail('must not yield')


def test_unsupported_platform_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'supports_dir_fd', set())
    with pytest.raises(service.ProjectGitSourceError) as caught, service._observe_root(str(tmp_path.resolve())):
        pytest.fail('must not yield')
    assert caught.value.code == 'project_git_source_platform_unsupported'


def test_partial_open_failure_closes_preceding_descriptors(tmp_path, monkeypatch):
    original = os.open
    descriptors = []
    def failing(*args, **kwargs):
        if len(descriptors) == 2:
            raise PermissionError('private path')
        descriptor = original(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor
    monkeypatch.setattr(os, 'open', failing)
    monkeypatch.setattr(os, 'supports_dir_fd', {*os.supports_dir_fd, failing})
    with pytest.raises(PermissionError), service._observe_root(str(tmp_path.resolve())):
        pytest.fail('must not yield')
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_service_preserves_interrupt_during_second_query(setup, monkeypatch):
    original = service.read_owned_git_binding
    calls = 0
    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'read_owned_git_binding', interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.read_project_git_source(setup[0])
