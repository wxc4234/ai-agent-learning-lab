"""Owned workspace persistence with real transactions on isolated PostgreSQL."""

from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import User, Workspace
from app.repositories.workspace.workspace_repository import (
    InvalidWorkspaceNameError,
    WorkspaceNotAccessibleError,
    create_workspace,
    require_owned_workspace,
)


@pytest.fixture
def owners(engine):
    with Session(engine) as session, session.begin():
        first = User(external_id="workspace-owner-a")
        second = User(external_id="workspace-owner-b")
        session.add_all([first, second])
        session.flush()
        return first.id, second.id


@pytest.mark.parametrize("name,expected", [
    ("项目", "项目"),
    (" \t我的 Agent 项目\n\u3000", "我的 Agent 项目"),
    ("a", "a"),
    ("中" * 100, "中" * 100),
    (" " + "a" * 100 + " ", "a" * 100),
])
def test_create_normalizes_name_and_persists_identity(engine, owners, name, expected):
    with Session(engine) as session, session.begin():
        workspace = create_workspace(session, user_id=owners[0], name=name)
        external_id = workspace.external_id
        assert UUID(hex=external_id).version == 4
        assert len(external_id) == 32
        assert workspace.id is not None
        assert workspace.name == expected
        assert workspace.created_at.tzinfo is not None
        assert workspace.user.id == owners[0]
        assert workspace in workspace.user.workspaces
    with Session(engine) as session:
        loaded = require_owned_workspace(session, user_id=owners[0], workspace_id=external_id)
        assert loaded.name == expected and loaded.user_id == owners[0]


@pytest.mark.parametrize("name", ["", " ", "\t\n\u3000", "a" * 101, "中" * 101])
def test_invalid_name_rejected_without_flushing_callers_pending_data(engine, owners, name):
    with Session(engine) as session:
        pending = User(external_id="pending-user")
        session.add(pending)
        with pytest.raises(InvalidWorkspaceNameError):
            create_workspace(session, user_id=owners[0], name=name)
        assert pending.id is None
        assert session.is_active
        session.rollback()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Workspace)) == 0


def test_duplicate_names_create_distinct_workspaces(engine, owners):
    with Session(engine) as session, session.begin():
        first = create_workspace(session, user_id=owners[0], name="同名")
        second = create_workspace(session, user_id=owners[0], name="同名")
        other = create_workspace(session, user_id=owners[1], name="同名")
        assert len({first.external_id, second.external_id, other.external_id}) == 3
        assert len({first.id, second.id, other.id}) == 3


def test_foreign_and_missing_raise_same_error_without_creating(engine, owners):
    with Session(engine) as session, session.begin():
        external_id = create_workspace(session, user_id=owners[0], name="私有项目").external_id
    errors = []
    with Session(engine) as session:
        for workspace_id in (external_id, "missing"):
            with pytest.raises(WorkspaceNotAccessibleError) as caught:
                require_owned_workspace(session, user_id=owners[1], workspace_id=workspace_id)
            errors.append((caught.value.code, str(caught.value)))
        assert errors[0] == errors[1]
        assert session.scalar(select(func.count()).select_from(Workspace)) == 1
        assert require_owned_workspace(session, user_id=owners[0], workspace_id=external_id).name == "私有项目"


def test_flush_is_invisible_to_other_connection_until_caller_commits(engine, owners):
    with Session(engine) as writer:
        workspace = create_workspace(writer, user_id=owners[0], name="待提交")
        external_id = workspace.external_id
        assert require_owned_workspace(writer, user_id=owners[0], workspace_id=external_id) is workspace
        with Session(engine) as observer:
            assert observer.scalar(select(func.count()).select_from(Workspace)) == 0
        writer.commit()
    with Session(engine) as observer:
        assert require_owned_workspace(observer, user_id=owners[0], workspace_id=external_id).name == "待提交"


def test_caller_rollback_removes_workspace_and_other_writes(engine, owners):
    with Session(engine) as session:
        user = session.get(User, owners[0])
        user.external_id = "changed-owner"
        create_workspace(session, user_id=owners[0], name="回滚")
        session.rollback()
    with Session(engine) as observer:
        assert observer.get(User, owners[0]).external_id == "workspace-owner-a"
        assert observer.scalar(select(func.count()).select_from(Workspace)) == 0


def test_unknown_user_preserves_integrity_error_and_caller_can_recover(engine, owners):
    with Session(engine) as session:
        with pytest.raises(IntegrityError):
            create_workspace(session, user_id=max(owners) + 100, name="不存在的用户")
        assert not session.is_active
        session.rollback()
        create_workspace(session, user_id=owners[0], name="恢复")
        session.commit()


@pytest.mark.parametrize("name", ["", "a" * 101])
def test_database_rejects_invalid_length_even_when_bypassing_repository(engine, owners, name):
    with Session(engine) as session:
        session.add(Workspace(external_id="direct", user_id=owners[0], name=name))
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()


def test_database_unique_external_id(engine, owners):
    with Session(engine) as session, session.begin():
        external_id = create_workspace(session, user_id=owners[0], name="甲").external_id
    with Session(engine) as session:
        session.add(Workspace(external_id=external_id, user_id=owners[1], name="乙"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_database_query_fault_is_not_an_access_error(engine, owners):
    with Session(engine) as session:
        session.execute(text("SET LOCAL search_path TO missing_workspace_schema"))
        with pytest.raises(DBAPIError):
            require_owned_workspace(session, user_id=owners[0], workspace_id="any")
        session.rollback()
        assert session.scalar(select(func.count()).select_from(Workspace)) == 0
