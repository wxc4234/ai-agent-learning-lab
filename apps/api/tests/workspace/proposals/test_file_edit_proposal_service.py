"""真实 PostgreSQL 与临时文件验证提案事务，不接触开发业务数据。"""

from dataclasses import FrozenInstanceError, asdict
from hashlib import sha256

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation, FileEditProposal, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.tasks.task_deletion_service import delete_workspace_task
from app.services.workspace.proposals import file_edit_proposal_service as service
from app.services.workspace.edits.workspace_edit_preview import EditPreviewError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from tests.workspace.directory import test_workspace_path as path_tests

root = path_tests.root
target = path_tests.target
database = path_tests.database


@pytest.fixture
def setup(database, engine, target, root, monkeypatch):
    sessions = []

    class ProposalSession(Session):
        closed = False

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

        def close(self):
            super().close()
            self.closed = True

    factory = sessionmaker(bind=engine, class_=ProposalSession, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", factory)
    file = root / "src" / "中文 file.txt"
    file.write_bytes(b"\xef\xbb\xbfold\r\n")
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    args.update(relative_path="./src/中文 file.txt", old_text="old", new_text="new")
    yield args, file, sessions, ProposalSession
    assert all(session.closed and not session.in_transaction() for session in sessions)


def count(engine):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(FileEditProposal))


def test_committed_snapshot_is_private_immutable_and_file_unchanged(setup, engine, root):
    args, file, sessions, _ = setup
    original = file.read_bytes()
    result = service.create_task_file_edit_proposal(**args)
    assert len(sessions) == 2 and all(session.closed for session in sessions)
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal))
        assert row.external_id == result.proposal_id and len(result.proposal_id) == 32
        assert row.bound_root == str(root)
        assert row.relative_path == "src/中文 file.txt"
        assert row.proposed_content.encode() == original.replace(b"old", b"new")
        assert row.baseline_sha256 == sha256(original).hexdigest()
        assert row.proposed_sha256 == sha256(row.proposed_content.encode()).hexdigest()
        assert row.diff and not row.diff_truncated
        assert row.status == result.status == "pending"
        assert result.created_at.tzinfo is not None
    assert set(asdict(result)) == {
        "proposal_id", "workspace_id", "task_id", "relative_path", "status",
        "baseline_sha256", "proposed_sha256", "diff_truncated", "created_at",
    }
    with pytest.raises(FrozenInstanceError):
        result.status = "approved"
    assert file.read_bytes() == original


def test_repeat_is_separate_proposal_and_task_delete_cascades(setup, engine):
    args, file, _, _ = setup
    first = service.create_task_file_edit_proposal(**args)
    second = service.create_task_file_edit_proposal(**args)
    assert first.proposal_id != second.proposal_id and count(engine) == 2
    with Session(engine) as session:
        delete_workspace_task(session, **{key: args[key] for key in ("user_id", "workspace_id", "task_id")})
    assert count(engine) == 0 and file.read_bytes() == b"\xef\xbb\xbfold\r\n"


@pytest.mark.parametrize("new_text", ["", "x" * 20000])
def test_empty_content_and_truncated_diff_keep_exact_proposed_content(setup, engine, new_text):
    args, file, _, _ = setup
    file.write_bytes(b"old")
    result = service.create_task_file_edit_proposal(**{**args, "new_text": new_text})
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal))
        assert row.proposed_content == new_text
        assert row.proposed_sha256 == sha256(new_text.encode()).hexdigest()
        assert row.diff_truncated is bool(new_text)
        assert result.diff_truncated == row.diff_truncated
    assert file.read_bytes() == b"old"


@pytest.mark.parametrize("kind", ["foreign", "unbound", "missing", "ambiguous"])
def test_initial_rejection_saves_nothing(setup, engine, target, kind):
    args, file, _, _ = setup
    expected = WorkspaceNotAccessibleError
    if kind == "foreign":
        args["user_id"] = target["other_id"]
    elif kind == "missing":
        args["task_id"] = "missing"
    elif kind == "unbound":
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = None
        expected = WorkspacePathError
    else:
        file.write_bytes(b"old old")
        expected = EditPreviewError
    before = file.read_bytes()
    with pytest.raises(expected):
        service.create_task_file_edit_proposal(**args)
    assert count(engine) == 0 and file.read_bytes() == before


@pytest.mark.parametrize("kind", ["owner", "conversation", "binding", "deleted", "external-edit"])
def test_reauthorizes_after_io_and_retains_same_read_snapshot(setup, engine, target, monkeypatch, kind):
    args, file, sessions, _ = setup
    original_preview = service.preview_task_file_replacement

    def preview(**kwargs):
        # 快照查询已关闭，耗时 I/O 阶段没有保存事务或行锁。
        assert len(sessions) == 1 and sessions[0].closed
        result = original_preview(**kwargs)
        with Session(engine) as session, session.begin():
            if kind == "owner":
                session.scalar(select(Workspace)).user_id = target["other_id"]
            elif kind == "conversation":
                session.get(Conversation, target["conversation_pk"]).user_id = target["other_id"]
            elif kind == "binding":
                session.scalar(select(Workspace)).root_path = "/changed"
        if kind == "deleted":
            with Session(engine) as session:
                delete_workspace_task(session, **{key: args[key] for key in ("user_id", "workspace_id", "task_id")})
        if kind == "external-edit":
            file.write_bytes(b"editor changed")
        return result

    monkeypatch.setattr(service, "preview_task_file_replacement", preview)
    if kind == "external-edit":
        result = service.create_task_file_edit_proposal(**args)
        assert result.baseline_sha256 == sha256(b"\xef\xbb\xbfold\r\n").hexdigest()
        assert file.read_bytes() == b"editor changed"
    else:
        expected = service.ProposalBindingChangedError if kind == "binding" else WorkspaceNotAccessibleError
        with pytest.raises(expected):
            service.create_task_file_edit_proposal(**args)
        assert count(engine) == 0 and file.read_bytes() == b"\xef\xbb\xbfold\r\n"


@pytest.mark.parametrize("stage", ["after-flush", "before-commit"])
def test_failure_rolls_back_and_never_returns_success(setup, engine, monkeypatch, stage):
    args, file, _, session_class = setup
    insert = service.insert_file_edit_proposal

    def fail(*args, **kwargs):
        raise RuntimeError("injected transaction failure")

    def inserted_then_fail(*args, **kwargs):
        insert(*args, **kwargs)
        fail()

    if stage == "after-flush":
        monkeypatch.setattr(service, "insert_file_edit_proposal", inserted_then_fail)
    else:
        event.listen(session_class, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            service.create_task_file_edit_proposal(**args)
    finally:
        if stage == "before-commit":
            event.remove(session_class, "before_commit", fail)
    assert count(engine) == 0 and file.read_bytes() == b"\xef\xbb\xbfold\r\n"


def test_write_lock_order_matches_delete(setup, database):
    args, _, _, _ = setup
    service.create_task_file_edit_proposal(**args)
    statements = [sql.lower() for sql in database[1] if "for update" in sql.lower()]
    assert len(statements) == 3
    assert all(f"from {name}" in sql for name, sql in zip(("workspaces", "tasks", "conversations"), statements))


def test_uncommitted_save_blocks_delete_and_rollback_releases_lock(setup, engine, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    args, _, _, _ = setup
    original_insert = service.insert_file_edit_proposal

    def verify_lock(*positional, **kwargs):
        row = original_insert(*positional, **kwargs)
        # 独立事务的删除必须等待保存持有的 Task 锁，不能制造孤儿提案。
        with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
            connection.execute(text("DELETE FROM tasks WHERE id=:id"), {"id": row.task_id})
        assert caught.value.orig.sqlstate == "55P03"
        raise RuntimeError("rollback saved proposal")

    monkeypatch.setattr(service, "insert_file_edit_proposal", verify_lock)
    with pytest.raises(RuntimeError, match="rollback saved proposal"):
        service.create_task_file_edit_proposal(**args)
    assert count(engine) == 0
    with Session(engine) as session:
        delete_workspace_task(session, **{key: args[key] for key in ("user_id", "workspace_id", "task_id")})


def test_uncommitted_deletion_blocks_save_then_rollback_allows_save(setup, engine, target):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task

    args, _, _, _ = setup
    # 模拟删除已拿到同一锁序，但尚未提交；用 PG lock_timeout 避免测试挂起。
    with Session(engine) as deleting:
        lock_owned_proposal_task(deleting, **{key: args[key] for key in ("user_id", "workspace_id", "task_id")})
        deleting.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": target["conversation_pk"]})
        deleting.execute(text("DELETE FROM tasks WHERE id=:id"), {"id": target["task_pk"]})
        with Session(engine) as saving:
            saving.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError) as caught:
                lock_owned_proposal_task(saving, **{key: args[key] for key in ("user_id", "workspace_id", "task_id")})
            assert caught.value.orig.sqlstate == "55P03"
        deleting.rollback()
    service.create_task_file_edit_proposal(**args)
    assert count(engine) == 1
