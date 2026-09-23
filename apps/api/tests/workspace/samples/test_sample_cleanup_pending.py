"""隔离PostgreSQL与真实临时文件验证关闭待办的两个提交边界。"""

from dataclasses import asdict
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace, WorkspaceSampleOrigin
from app.services.tasks.task_deletion_service import TaskSampleBoundError, delete_workspace_task
from app.services.workspace.directory.workspace_binding import (
    WorkspaceAlreadyBoundError,
    bind_workspace_directory,
)
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError, TaskSampleBindings
from app.services.workspace.samples.temporary_proposal_sample import TemporarySampleError
from tests.workspace.samples.test_task_sample_binding import root, setup, target

__all__ = ["setup", "target"]


def test_normal_close_retains_pending_until_safe_file_cleanup(setup, engine, monkeypatch):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    original_close = service._registry.close

    def inspect_pending(handle):
        with Session(engine) as session:
            workspace = session.scalar(select(Workspace))
            origin = session.get(WorkspaceSampleOrigin, workspace.id)
            assert workspace.root_path is None
            assert origin.lifecycle_state == "cleanup_pending"
            assert origin.root_path == str(path)
        assert path.exists()
        assert asdict(service.read_status(**scope)) == {
            "status": "sealed", "sealed_reason": "cleanup_pending",
        }
        assert asdict(TaskSampleBindings().read_status(**scope)) == {
            "status": "sealed", "sealed_reason": "cleanup_pending",
        }
        return original_close(handle)

    monkeypatch.setattr(service._registry, "close", inspect_pending)
    service.close(**scope)
    with Session(engine) as session:
        assert session.scalar(select(WorkspaceSampleOrigin)) is None
        assert session.scalar(select(Workspace.root_path)) is None
    assert not path.exists()
    assert service.read_status(**scope).status == "missing"


def test_cleanup_failure_preserves_pending_and_blocks_rebinding(setup, engine, tmp_path):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    (path / "unknown").write_bytes(b"keep")

    with pytest.raises(TemporarySampleError):
        service.close(**scope)
    with Session(engine) as session:
        workspace = session.scalar(select(Workspace))
        origin = session.get(WorkspaceSampleOrigin, workspace.id)
        assert workspace.root_path is None
        assert origin.lifecycle_state == "cleanup_pending"
        assert origin.root_path == str(path)
    assert path.exists()
    assert asdict(service.read_status(**scope)) == {
        "status": "sealed", "sealed_reason": "cleanup_pending",
    }
    assert asdict(TaskSampleBindings().read_status(**scope)) == {
        "status": "sealed", "sealed_reason": "cleanup_pending",
    }

    with Session(engine) as session, pytest.raises(TaskSampleBoundError):
        delete_workspace_task(session, **scope)
    with pytest.raises(TaskSampleBindingError) as caught:
        TaskSampleBindings().bind(**scope)
    assert caught.value.code == "task_sample_already_bound"

    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    with Session(engine) as session, pytest.raises(WorkspaceAlreadyBoundError):
        bind_workspace_directory(
            session,
            user_id=scope["user_id"],
            workspace_id=scope["workspace_id"],
            root_path=str(ordinary),
        )
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) is None
        assert session.scalar(select(WorkspaceSampleOrigin.lifecycle_state)) == "cleanup_pending"


@pytest.mark.parametrize("outcome", ["interrupted", "deferred"])
def test_cleanup_not_confirmed_keeps_pending_evidence(setup, engine, monkeypatch, outcome):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))

    def not_confirmed(_handle):
        if outcome == "interrupted":
            raise KeyboardInterrupt()
        return False

    # 只在本次调用模拟中断/延期；夹具退出时恢复真实清理方法。
    with monkeypatch.context() as patch:
        patch.setattr(service._registry, "close", not_confirmed)
        with pytest.raises(KeyboardInterrupt if outcome == "interrupted" else TaskSampleBindingError):
            service.close(**scope)
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) is None
        assert session.scalar(select(WorkspaceSampleOrigin.lifecycle_state)) == "cleanup_pending"
    assert path.exists()
    assert service.read_status(**scope).status == "sealed"
    with pytest.raises(TaskSampleBindingError), service.borrow(**scope):
        pytest.fail("cleanup uncertainty must not reopen the sample")


@pytest.mark.parametrize("timing", ["before", "after"])
def test_final_record_commit_failure_never_reopens_old_binding(setup, engine, monkeypatch, timing):
    service, scope, tracked, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    original_commit = tracked.commit
    commits = 0

    def fail_second_commit(self):
        nonlocal commits
        commits += 1
        if commits == 2 and timing == "before":
            raise RuntimeError("cleanup record confirmation lost")
        result = original_commit(self)
        if commits == 2 and timing == "after":
            raise RuntimeError("cleanup record confirmation lost")
        return result

    monkeypatch.setattr(tracked, "commit", fail_second_commit)
    with pytest.raises(RuntimeError, match="confirmation lost"):
        service.close(**scope)
    assert commits == 2
    assert not path.exists()
    # 原因只来自持久记录：文件已删但待办仍在时仍报待办；记录已删则只报一般封锁。
    assert asdict(service.read_status(**scope)) == {
        "status": "sealed",
        "sealed_reason": "cleanup_pending" if timing == "before" else "unavailable",
    }
    with pytest.raises(TaskSampleBindingError), service.borrow(**scope):
        pytest.fail("unknown final commit must not reopen the old handle")
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) is None
        origin = session.scalar(select(WorkspaceSampleOrigin))
        assert (origin.lifecycle_state if origin is not None else None) == (
            "cleanup_pending" if timing == "before" else None
        )
