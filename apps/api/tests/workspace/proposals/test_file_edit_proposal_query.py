"""提案查询的真实 PostgreSQL 授权边界与只读公开投影。"""

from dataclasses import FrozenInstanceError, asdict

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, FileEditProposal, Task, Workspace
from app.repositories.workspace.file_edit_proposal_repository import read_owned_file_edit_proposal
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.tasks.task_deletion_service import delete_workspace_task
from app.services.workspace.proposals import file_edit_proposal_service as service
from tests.workspace.proposals import test_file_edit_proposal_service as save_tests

root = save_tests.root
target = save_tests.target
database = save_tests.database
setup = save_tests.setup


@pytest.fixture
def saved(setup):
    args, file, sessions, _ = setup
    created = service.create_task_file_edit_proposal(**args)
    query = {key: args[key] for key in ("user_id", "workspace_id", "task_id")}
    query["proposal_id"] = created.proposal_id
    return query, file, sessions


def test_public_snapshot_uses_one_select_and_closes_session(saved, database, monkeypatch):
    query, file, sessions = saved
    original = file.read_bytes()
    statements = database[1]
    statements.clear()
    monkeypatch.setattr(sessions[0].__class__, "commit", lambda self: pytest.fail("read must not commit"))
    result = service.get_task_file_edit_proposal(**query)
    assert sessions[-1].closed and not sessions[-1].in_transaction()
    assert set(asdict(result)) == {
        "proposal_id", "workspace_id", "task_id", "relative_path", "status",
        "baseline_sha256", "proposed_sha256", "diff", "diff_truncated", "created_at",
    }
    assert result.proposal_id == query["proposal_id"]
    assert result.relative_path == "src/中文 file.txt" and result.status == "pending"
    assert result.diff and not result.diff_truncated and result.created_at.tzinfo is not None
    assert len(statements) == 1
    sql = statements[0].lower()
    assert sql.lstrip().startswith("select") and "for update" not in sql
    assert "bound_root" not in sql and "proposed_content" not in sql
    assert file.read_bytes() == original
    with pytest.raises(FrozenInstanceError):
        result.status = "approved"


@pytest.mark.parametrize("kind", [
    "foreign-user", "missing-workspace", "missing-task", "missing-proposal",
    "sibling-task", "wrong-workspace", "foreign-conversation", "missing-conversation",
    "changed-owner", "deleted-task",
])
def test_all_inaccessible_resources_use_same_safe_error(saved, engine, target, kind):
    query, _, sessions = saved
    expected_message = str(WorkspaceNotAccessibleError())
    with Session(engine) as session, session.begin():
        if kind == "foreign-user":
            query["user_id"] = target["other_id"]
        elif kind.startswith("missing-") and kind != "missing-conversation":
            query[kind.removeprefix("missing-") + "_id"] = "PRIVATE-missing"
        elif kind == "sibling-task":
            query["task_id"] = "d" * 32
        elif kind == "wrong-workspace":
            session.add(Workspace(external_id="different", user_id=target["user_id"], name="其他"))
            query["workspace_id"] = "different"
        elif kind == "foreign-conversation":
            session.get(Conversation, target["conversation_pk"]).user_id = target["other_id"]
        elif kind == "missing-conversation":
            session.delete(session.get(Conversation, target["conversation_pk"]))
        elif kind == "changed-owner":
            session.scalar(select(Workspace)).user_id = target["other_id"]
    if kind == "deleted-task":
        with Session(engine) as session:
            delete_workspace_task(session, **{key: query[key] for key in ("user_id", "workspace_id", "task_id")})
    with pytest.raises(WorkspaceNotAccessibleError) as caught:
        service.get_task_file_edit_proposal(**query)
    assert str(caught.value) == expected_message
    assert sessions[-1].closed and not sessions[-1].in_transaction()


def test_saved_review_survives_file_deletion_and_binding_change(saved, engine, monkeypatch):
    query, file, _ = saved
    before = service.get_task_file_edit_proposal(**query)
    file.unlink()
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = None
    monkeypatch.setattr(service, "preview_task_file_replacement", lambda **kwargs: pytest.fail("must not regenerate"))
    assert service.get_task_file_edit_proposal(**query) == before
    assert not file.exists()


def test_truncated_diff_is_returned_exactly_without_private_content(setup, engine):
    args, file, _sessions, _ = setup
    file.write_text("old")
    created = service.create_task_file_edit_proposal(**{**args, "new_text": "x" * 20000})
    result = service.get_task_file_edit_proposal(
        **{key: args[key] for key in ("user_id", "workspace_id", "task_id")}, proposal_id=created.proposal_id,
    )
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal))
        assert result.diff == row.diff and result.diff_truncated
        assert len(result.diff) == 16384
        assert result.baseline_sha256 == row.baseline_sha256
        assert result.proposed_sha256 == row.proposed_sha256
    assert "x" * 20000 not in repr(asdict(result))
    assert file.read_text() == "old"


def test_repository_does_not_flush_or_commit_callers_pending_changes(saved, engine, target):
    query, _, _ = saved
    with Session(engine, autoflush=True) as session:
        task = session.get(Task, target["task_pk"])
        task.title = "uncommitted"
        row = read_owned_file_edit_proposal(session, **query)
        assert row["proposal_id"] == query["proposal_id"] and task in session.dirty
        with Session(engine) as reader:
            assert reader.get(Task, target["task_pk"]).title == "空任务"
        session.rollback()


def test_database_failure_propagates_and_session_closes(saved, monkeypatch):
    query, _, sessions = saved

    def fail(*args, **kwargs):
        raise RuntimeError("query unavailable")

    monkeypatch.setattr(service, "read_owned_file_edit_proposal", fail)
    with pytest.raises(RuntimeError, match="query unavailable"):
        service.get_task_file_edit_proposal(**query)
    assert sessions[-1].closed
