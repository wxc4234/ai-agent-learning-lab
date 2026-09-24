"""Task 授权→借用→读取→归还→快照，使用隔离 PostgreSQL 与真实目录。"""

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace, WorkspaceSampleOrigin
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import task_sample_snapshot as service
from app.services.runtime.command.task_command_source import TaskCommandSourceUnavailable
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError
from tests.runtime.command.test_task_command_source import lab as lab  # noqa: PLC0414
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414


def create(lab, target, **changes):
    return service.create_task_sandbox_snapshot(
        bindings=lab[0], **({'user_id': target['user_id'], 'conversation_id': target['conversation_id']} | changes),
    )


def bound_root(engine):
    with Session(engine) as session:
        return Path(session.scalar(select(Workspace.root_path)))


def test_snapshot_created_after_release_and_independent(lab, target, engine, monkeypatch):
    bindings, scope, sessions = lab
    bindings.bind(**scope)
    root = bound_root(engine)
    path = root / 'example.txt'
    content = b'\xef\xbb\xbf' + '中文\r\n'.encode()
    path.write_bytes(content)
    before = (root.stat().st_mode, path.stat().st_mode, path.stat().st_ino, path.stat().st_mtime_ns)
    original = service.create_sandbox_snapshot
    def after_release(**kwargs):
        assert bindings.read_status(**scope).status == 'ready'
        assert all(s.closed and not s.in_transaction() for s in sessions)
        return original(**kwargs)
    monkeypatch.setattr(service, 'create_sandbox_snapshot', after_release)
    snapshot = create(lab, target)
    try:
        assert snapshot.root != root and snapshot.root.parent != root
        assert (snapshot.root / 'example.txt').read_bytes() == content
        assert (root.stat().st_mode, path.stat().st_mode, path.stat().st_ino, path.stat().st_mtime_ns) == before
        with Session(engine) as session:
            origin = session.scalar(select(WorkspaceSampleOrigin))
            assert origin.root_path == str(root) and origin.lifecycle_state == 'active'
        path.write_bytes(b'changed later')
        assert (snapshot.root / 'example.txt').read_bytes() == content
        bindings.close(**scope)
        assert not root.exists()
        assert samples.confirm_sandbox_sample_source(snapshot)
    finally:
        samples.cleanup_sandbox_sample(snapshot)


@pytest.mark.parametrize('kind', ['wrong_user', 'missing', 'busy', 'read_failure', 'exit_failure'])
def test_failure_before_factory_creates_no_target(lab, target, engine, monkeypatch, kind):
    bindings, scope, _ = lab
    if kind != 'missing':
        bindings.bind(**scope)
    calls = []
    monkeypatch.setattr(service, 'create_sandbox_snapshot', lambda **kwargs: calls.append(kwargs))
    changes = {'user_id': target['other_id']} if kind == 'wrong_user' else {}
    if kind == 'read_failure':
        (bound_root(engine) / 'example.txt').chmod(0o644)
    if kind == 'exit_failure':
        original = service.borrow_task_command_source
        @contextmanager
        def failing_exit(**kwargs):
            with original(**kwargs) as source:
                yield source
                raise OSError('injected return failure')
        monkeypatch.setattr(service, 'borrow_task_command_source', failing_exit)
    try:
        if kind == 'busy':
            with bindings.borrow(**scope), pytest.raises(TaskSampleBindingError):
                create(lab, target)
        else:
            with pytest.raises((TaskSampleBindingError, ConversationNotAccessibleError, TaskCommandSourceUnavailable, OSError)):
                create(lab, target, **changes)
        assert calls == []
    finally:
        if kind == 'read_failure':
            (bound_root(engine) / 'example.txt').chmod(0o600)


def test_target_failure_keeps_source_ready_and_partial_evidence(lab, target, engine, monkeypatch):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    root = bound_root(engine)
    def fail(fd, content):
        raise OSError('injected target write failure')
    with monkeypatch.context() as patcher:
        patcher.setattr(samples.os, 'write', fail)
        with pytest.raises(samples.SandboxSampleCreationUnconfirmed) as caught:
            create(lab, target)
    assert root.exists() and (root / 'example.txt').read_bytes() == b'old\n'
    assert bindings.read_status(**scope).status == 'ready'
    error = caught.value
    assert error.root is not None and error.token not in samples._ACTIVE_SAMPLES
    # 测试拥有者收尾自己的故障现场，不提供生产递归删除入口。
    (error.root / 'example.txt').unlink()
    error.root.rmdir()
    error.root.parent.rmdir()
