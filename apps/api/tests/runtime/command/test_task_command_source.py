"""隔离 PostgreSQL 与真实临时目录验证来源借用，不调用 Docker 或模型。"""

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation, Task, Workspace, WorkspaceSampleOrigin
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError
from app.services.runtime.agent import tool_execution_context as contexts
from app.services.runtime.command import task_command_source as service
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414


@pytest.fixture
def lab(setup, engine, monkeypatch):
    bindings, scope, tracked, sessions = setup
    monkeypatch.setattr(contexts, 'SessionLocal', sessionmaker(bind=engine, class_=tracked))
    return bindings, scope, sessions


def borrow(lab, target, **overrides):
    return service.borrow_task_command_source(
        bindings=lab[0], **({'user_id': target['user_id'], 'conversation_id': target['conversation_id']} | overrides),
    )


def test_owned_source_exclusive_readonly_and_scoped(lab, target, engine):
    bindings, scope, sessions = lab
    bindings.bind(**scope)
    with borrow(lab, target) as source:
        path = source.root
        before = (path / 'example.txt').read_bytes()
        assert source.context.task_id == target['task_id']
        assert str(path) not in repr(source)
        assert all(s.closed and not s.in_transaction() for s in sessions)
        assert bindings.read_status(**scope).status == 'busy'
        with pytest.raises(TaskSampleBindingError), borrow(lab, target):
            pytest.fail('second borrower')
        with pytest.raises(TaskSampleBindingError):
            bindings.close(**scope)
    with pytest.raises(service.TaskCommandSourceUnavailable):
        _ = source.root
    assert bindings.read_status(**scope).status == 'ready'
    assert (path / 'example.txt').read_bytes() == before
    with Session(engine) as session:
        assert session.scalar(select(WorkspaceSampleOrigin)).lifecycle_state == 'active'
        assert session.scalar(select(Workspace.root_path)) == str(path)
    with borrow(lab, target) as again:
        assert again.root == path
    bindings.close(**scope)
    assert not path.exists()


@pytest.mark.parametrize('kind', ['wrong_user', 'missing_conversation', 'sibling', 'no_binding'])
def test_unavailable_scope(lab, target, kind):
    if kind != 'no_binding':
        lab[0].bind(**lab[1])
    changes = {}
    if kind == 'wrong_user':
        changes['user_id'] = target['other_id']
    elif kind == 'missing_conversation':
        changes['conversation_id'] = 'f' * 32
    elif kind == 'sibling':
        changes['conversation_id'] = 'e' * 32
    with pytest.raises((TaskSampleBindingError, ConversationNotAccessibleError)), borrow(lab, target, **changes):
        pytest.fail('unavailable source')


@pytest.mark.parametrize('kind', ['root', 'origin', 'pending'])
def test_binding_changed_after_ready_is_rejected(lab, target, engine, kind):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    assert bindings.read_status(**scope).status == 'ready'
    with Session(engine) as session, session.begin():
        if kind == 'root':
            session.scalar(select(Workspace)).root_path = '/changed'
        elif kind == 'origin':
            session.delete(session.scalar(select(WorkspaceSampleOrigin)))
        else:
            session.scalar(select(WorkspaceSampleOrigin)).lifecycle_state = 'cleanup_pending'
    with pytest.raises(TaskSampleBindingError), borrow(lab, target):
        pytest.fail('stale ready')
    binding = next(iter(bindings._bindings.values()))
    assert not binding.busy and binding.state == 'uncertain'


@pytest.mark.parametrize('error_type', [RuntimeError, asyncio.CancelledError])
def test_exception_invalidates_view_releases_busy_and_seals(lab, target, error_type):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    with pytest.raises(error_type), borrow(lab, target) as source:
        path = source.root
        raise error_type('consumer failed')
    with pytest.raises(service.TaskCommandSourceUnavailable):
        _ = source.root
    binding = next(iter(bindings._bindings.values()))
    assert not binding.busy and path.exists()
    assert bindings.read_status(**scope).status == 'sealed'
    with pytest.raises(TaskSampleBindingError), borrow(lab, target):
        pytest.fail('sealed')


def test_conversation_moved_between_load_and_borrow(lab, target, engine, monkeypatch):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    original = bindings.borrow
    @contextmanager
    def move(**kwargs):
        with original(**kwargs) as sample:
            with Session(engine) as session, session.begin():
                sibling = Task(external_id='f' * 32, title='迁移目标', workspace=session.scalar(select(Workspace)))
                session.add(sibling)
                session.flush()
                session.get(Conversation, target['conversation_pk']).task_id = sibling.id
            yield sample
    monkeypatch.setattr(bindings, 'borrow', move)
    with pytest.raises(service.TaskCommandSourceUnavailable), borrow(lab, target):
        pytest.fail('changed conversation')
    assert not next(iter(bindings._bindings.values())).busy


def test_forked_view_refuses_path(lab, target, monkeypatch):
    lab[0].bind(**lab[1])
    with borrow(lab, target) as source, monkeypatch.context() as patcher:
        patcher.setattr(service.os, 'getpid', lambda: source._lifetime.pid + 1)
        with pytest.raises(service.TaskCommandSourceUnavailable):
            _ = source.root


@pytest.mark.parametrize('field', ['root_path', 'workspace_id', 'task_id'])
def test_no_independent_source_selection(field):
    with pytest.raises(TypeError), service.borrow_task_command_source(
        user_id=1, conversation_id='c' * 32, bindings=None, **{field: '/untrusted'},
    ):
        pytest.fail('untrusted selection')


def test_second_authorization_error_does_not_leak_borrow(lab, target, monkeypatch):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    original = service.load_tool_execution_context
    count = 0
    def fail(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('read unavailable')
        return original(**kwargs)
    monkeypatch.setattr(service, 'load_tool_execution_context', fail)
    with pytest.raises(OSError), borrow(lab, target):
        pytest.fail('unknown authorization')
    binding = next(iter(bindings._bindings.values()))
    assert not binding.busy and binding.state == 'uncertain'


def test_other_workspace_owner_is_rejected_before_borrow(lab, target, engine):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).user_id = target['other_id']
    with pytest.raises(ConversationNotAccessibleError), borrow(lab, target):
        pytest.fail('foreign workspace')
    binding = next(iter(bindings._bindings.values()))
    assert not binding.busy and binding.state == 'ready'
