"""真实 PostgreSQL 事务、行锁与临时目录验证本地绑定。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, Workspace
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    require_owned_workspace_for_update,
)
from app.services.workspace import workspace_binding as service
from app.services.workspace.workspace_directory import WorkspaceDirectoryError


@pytest.fixture
def setup_binding(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local")
    with Session(engine) as session, session.begin():
        owner = User(external_id="local-owner-v1")
        session.add(owner)
        session.flush()
        owner_id = owner.id
        session.add(Workspace(external_id="workspace", user_id=owner_id, name="项目"))
    first = tmp_path / "中文 项目"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    return owner_id, first, second


def bind(session, owner_id, path, workspace_id="workspace"):
    return service.bind_workspace_directory(
        session, user_id=owner_id, workspace_id=workspace_id, root_path=str(path),
    )


def stored(engine):
    with Session(engine) as session:
        return session.scalar(select(Workspace.root_path))


@pytest.mark.parametrize("expire", [True, False])
def test_success_is_committed_detached_and_repeatable(engine, setup_binding, expire):
    owner, first, _ = setup_binding
    link = first.parent / "entry"
    link.symlink_to(first, target_is_directory=True)
    with Session(engine, expire_on_commit=expire) as session:
        result = bind(session, owner, link)
        assert not session.in_transaction()
        assert stored(engine) == str(first.resolve())
        assert bind(session, owner, first) == result
        assert not session.in_transaction()
    assert result.external_id == "workspace" and result.name == "项目"
    assert result.root_path == str(first.resolve())
    with pytest.raises(FrozenInstanceError):
        result.root_path = "changed"


def test_different_directory_rejected_and_original_preserved(engine, setup_binding):
    owner, first, second = setup_binding
    with Session(engine) as session:
        bind(session, owner, first)
        with pytest.raises(service.WorkspaceAlreadyBoundError):
            bind(session, owner, second)
        assert not session.in_transaction()
        assert stored(engine) == str(first.resolve())
        bind(session, owner, first)


@pytest.mark.parametrize("already_bound", [False, True])
def test_invalid_directory_rolls_back_without_changes(engine, setup_binding, already_bound):
    owner, first, _ = setup_binding
    with Session(engine) as session:
        if already_bound:
            bind(session, owner, first)
        with pytest.raises(WorkspaceDirectoryError):
            bind(session, owner, first / "missing")
        assert not session.in_transaction()
        assert stored(engine) == (str(first.resolve()) if already_bound else None)


def test_repeat_revalidates_removed_directory(engine, setup_binding):
    owner, first, _ = setup_binding
    with Session(engine) as session:
        bind(session, owner, first)
        first.rmdir()
        with pytest.raises(WorkspaceDirectoryError):
            bind(session, owner, first)
        assert stored(engine) == str(first)


@pytest.mark.parametrize("kind", ["unknown", "wrong_owner"])
def test_denied_workspace_never_inspects_filesystem(engine, setup_binding, monkeypatch, kind):
    owner, first, _ = setup_binding

    def forbidden(path):
        pytest.fail("不可访问资源不应检查文件系统")

    monkeypatch.setattr(service, "validate_workspace_directory", forbidden)
    with Session(engine) as session:
        with pytest.raises(WorkspaceNotAccessibleError):
            bind(session, owner if kind == "unknown" else owner + 100, first,
                 workspace_id="missing" if kind == "unknown" else "workspace")
        assert not session.in_transaction()
    assert stored(engine) is None


@pytest.mark.parametrize("kind", ["explicit", "read", "pending", "flushed"])
def test_existing_transaction_is_untouched(engine, setup_binding, kind):
    owner, first, _ = setup_binding
    with Session(engine) as session:
        if kind == "explicit":
            session.begin()
        elif kind == "read":
            session.execute(select(Workspace))
        else:
            session.add(User(external_id="pending-owner"))
            if kind == "flushed":
                session.flush()
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match="无活动事务"):
            bind(session, owner, first)
        assert session.get_transaction() is transaction and transaction.is_active
        session.commit()
    assert stored(engine) is None


def test_commit_failure_after_flush_rolls_back_and_allows_retry(engine, setup_binding, monkeypatch):
    owner, first, _ = setup_binding
    with Session(engine) as session:
        def fail_commit():
            session.flush()
            assert session.scalar(select(Workspace.root_path)) == str(first.resolve())
            raise OperationalError("COMMIT", None, RuntimeError("simulated"))

        with monkeypatch.context() as patch:
            patch.setattr(session, "commit", fail_commit)
            with pytest.raises(OperationalError):
                bind(session, owner, first)
        assert not session.in_transaction()
        assert stored(engine) is None
        bind(session, owner, first)


def test_lock_query_refreshes_stale_identity_and_does_not_flush(engine, setup_binding):
    owner, first, _ = setup_binding
    with Session(engine, expire_on_commit=False) as session:
        cached = session.scalar(select(Workspace))
        session.commit()
        with Session(engine) as writer:
            bind(writer, owner, first)
        assert cached.root_path is None
        pending = User(external_id="pending-not-flushed")
        session.add(pending)
        record = require_owned_workspace_for_update(session, user_id=owner, workspace_id="workspace")
        assert record is cached and record.root_path == str(first.resolve())
        assert pending.id is None and pending in session.new
        session.rollback()


@pytest.mark.parametrize("same_directory", [True, False])
def test_concurrent_binding_waits_and_observes_committed_path(engine, setup_binding, monkeypatch, same_directory):
    owner, first, second = setup_binding
    locked = Event()
    release = Event()
    waiter_started = Event()
    original = service.validate_workspace_directory
    waiter_pid = []

    def pause_first(path):
        if path == str(first) and not locked.is_set():
            locked.set()
            assert release.wait(10), "test release timed out"
        return original(path)

    monkeypatch.setattr(service, "validate_workspace_directory", pause_first)

    def worker(path, waiter=False):
        with Session(engine) as session:
            if waiter:
                # 确认第二个连接的后端 PID；结束准备事务后才调用绑定服务。
                waiter_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
                session.commit()
                waiter_started.set()
            try:
                return bind(session, owner, path)
            except service.WorkspaceAlreadyBoundError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(worker, first)
        try:
            assert locked.wait(10)
            second_future = pool.submit(worker, first if same_directory else second, True)
            assert waiter_started.wait(10)
            # 检查数据库实际等待，不用固定 sleep 冒充并发竞争证据。
            deadline = monotonic() + 5
            blocked = False
            with engine.connect() as observer:
                while monotonic() < deadline:
                    blocked = observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": waiter_pid[0]})
                    if blocked:
                        break
                    sleep(0.02)
            assert blocked
        finally:
            release.set()
        first_result = first_future.result(timeout=10)
        second_result = second_future.result(timeout=10)
    assert second_result == (first_result if same_directory else "conflict")
    assert stored(engine) == str(first.resolve())
