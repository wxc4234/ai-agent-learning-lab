"""隔离PostgreSQL验证样例来源与目录绑定的同事务生命周期。"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Task, Workspace, WorkspaceSampleOrigin
from app.services.tasks.task_deletion_service import (
    TaskSampleBoundError,
    delete_workspace_task,
)
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError, TaskSampleBindings
from tests.workspace.samples.test_task_sample_binding import root, setup, target

__all__ = ["setup", "target"]


def test_bind_and_close_commit_origin_with_workspace_root(setup, engine, target):
    service, scope, _, _ = setup
    with Session(engine) as session:
        assert session.scalar(select(WorkspaceSampleOrigin)) is None

    service.bind(**scope)
    path = Path(root(engine))
    with Session(engine) as session:
        origin = session.scalar(select(WorkspaceSampleOrigin))
        workspace = session.scalar(select(Workspace))
        assert origin.workspace_id == workspace.id
        assert origin.task_id == target["task_pk"]
        assert origin.root_path == workspace.root_path == str(path)
        assert origin.created_at is not None

    service.close(**scope)
    with Session(engine) as session:
        assert session.scalar(select(Workspace.root_path)) is None
        assert session.scalar(select(WorkspaceSampleOrigin)) is None
    assert not path.exists()


def test_ordinary_project_directory_has_no_sample_origin(setup, engine, tmp_path):
    service, scope, _, _ = setup
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = str(tmp_path)
    with pytest.raises(TaskSampleBindingError) as caught:
        service.bind(**scope)
    assert caught.value.code == "task_sample_already_bound"
    with Session(engine) as session:
        assert session.scalar(select(WorkspaceSampleOrigin)) is None
        assert session.scalar(select(Workspace.root_path)) == str(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_new_process_cannot_adopt_persisted_origin(setup, engine):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = root(engine)
    fresh = TaskSampleBindings()
    assert fresh.read_status(**scope).status == "sealed"
    with pytest.raises(TaskSampleBindingError), fresh.borrow(**scope):
        pytest.fail("persistent provenance is not an execution handle")
    with Session(engine) as session:
        assert session.scalar(select(WorkspaceSampleOrigin.root_path)) == path
    service.close(**scope)


@pytest.mark.parametrize("change", ["root", "task", "unbound"])
def test_lost_registration_with_mismatched_origin_stays_sealed(setup, engine, change):
    service, scope, _, _ = setup
    service.bind(**scope)
    fresh = TaskSampleBindings()
    with Session(engine) as session, session.begin():
        origin = session.scalar(select(WorkspaceSampleOrigin))
        if change == "root":
            origin.root_path = "/different"
        elif change == "task":
            origin.task_id = session.scalar(select(Task.id).where(Task.external_id == "d" * 32))
        else:
            session.scalar(select(Workspace)).root_path = None
    assert fresh.read_status(**scope).status == "sealed"
    with pytest.raises(TaskSampleBindingError), fresh.borrow(**scope):
        pytest.fail("lost registration must not regain execution access")


def test_ordinary_directory_without_origin_stays_missing(setup, engine, tmp_path):
    _, scope, _, _ = setup
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = str(tmp_path)
    assert TaskSampleBindings().read_status(**scope).status == "missing"


def test_other_task_in_sample_workspace_is_not_reported_missing(setup):
    service, scope, _, _ = setup
    service.bind(**scope)
    sibling = {**scope, "task_id": "d" * 32}
    assert service.read_status(**sibling).status == "sealed"
    assert TaskSampleBindings().read_status(**sibling).status == "sealed"
    service.close(**scope)


@pytest.mark.parametrize("change", ["root", "task", "missing", "cleanup_pending"])
def test_origin_mismatch_seals_borrow_and_refuses_close(setup, engine, change):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    with Session(engine) as session, session.begin():
        origin = session.scalar(select(WorkspaceSampleOrigin))
        if change == "root":
            origin.root_path = "/different"
        elif change == "task":
            origin.task_id = session.scalar(select(Task.id).where(Task.external_id == "d" * 32))
        elif change == "cleanup_pending":
            origin.lifecycle_state = "cleanup_pending"
        else:
            session.delete(origin)
    assert service.read_status(**scope).status == "sealed"
    with pytest.raises(TaskSampleBindingError), service.borrow(**scope):
        pytest.fail("mismatched provenance must not authorize files")
    with pytest.raises(TaskSampleBindingError):
        service.close(**scope)
    assert path.exists() and root(engine) == str(path)


def test_source_task_deletion_rejected_until_normal_close(setup, engine, target):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    with Session(engine) as session:
        with pytest.raises(TaskSampleBoundError):
            delete_workspace_task(session, **scope)
        assert not session.in_transaction()
    with Session(engine) as session:
        assert session.get(Task, target["task_pk"]) is not None
        assert session.scalar(select(WorkspaceSampleOrigin)) is not None
    assert path.exists()

    service.close(**scope)
    with Session(engine) as session:
        result = delete_workspace_task(session, **scope)
    assert result.task_id == scope["task_id"]
    assert not path.exists()


def test_unrelated_task_deletion_keeps_sample_origin(setup, engine, target):
    service, scope, _, _ = setup
    service.bind(**scope)
    with Session(engine) as session:
        result = delete_workspace_task(
            session,
            user_id=scope["user_id"],
            workspace_id=scope["workspace_id"],
            task_id="d" * 32,
        )
    assert result.task_id == "d" * 32
    with Session(engine) as session:
        assert session.scalar(select(WorkspaceSampleOrigin.task_id)) == target["task_pk"]
    assert service.read_status(**scope).status == "ready"
    service.close(**scope)
