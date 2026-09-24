"""真实隔离 PostgreSQL 验证补丁与字符串替换共享提案事务。"""

from dataclasses import asdict
from difflib import unified_diff
from hashlib import sha256

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import FileEditProposal
from app.services.workspace.edits.workspace_unified_patch import UnifiedPatchError
from app.services.workspace.proposals import file_edit_proposal_service as service
from tests.workspace.proposals import test_file_edit_proposal_service as existing

root = existing.root
target = existing.target
database = existing.database
setup = existing.setup


@pytest.fixture
def patch_setup(setup):
    args, file, sessions, session_class = setup
    # 复用已有真实归属与事务夹具，只将输入换成补丁支持的 LF 文件。
    file.write_bytes(b"old\n")
    args.pop("old_text")
    args.pop("new_text")
    args["patch"] = make_patch("old\n", "new\n")
    return args, file, sessions, session_class


def make_patch(before, after):
    return "".join(unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/src/中文 file.txt", tofile="b/src/中文 file.txt",
    ))


@pytest.mark.parametrize("before,after", [
    ("old\n", "new\n"),
    ("\ufeffold\n", "\ufeffnew\n"),
    ("old\n", ""),
    ("", "new\n"),
    ("old\n", "x" * 20000 + "\n"),
    ("a\nb\nc\nd\ne\nf\ng\nh\ni\n", "A\nb\nc\nd\ne\nf\ng\nh\nI\n"),
])
def test_saved_candidate_and_query_share_existing_protocol(patch_setup, engine, root, before, after):
    args, file, sessions, _ = patch_setup
    file.write_text(before, encoding="utf-8")
    args["patch"] = make_patch(before, after)
    original = file.read_bytes()
    result = service.create_task_file_patch_proposal(**args)
    assert len(sessions) == 2 and all(session.closed for session in sessions)
    assert result.status == "pending"
    assert result.baseline_sha256 == sha256(original).hexdigest()
    assert result.proposed_sha256 == sha256(after.encode()).hexdigest()
    with Session(engine) as session:
        row = session.scalar(select(FileEditProposal))
        assert row.external_id == result.proposal_id
        assert row.bound_root == str(root)
        assert row.relative_path == "src/中文 file.txt"
        assert row.proposed_content == after
        assert row.diff_truncated is (len(after) > 20000)
        assert row.proposed_sha256 == result.proposed_sha256
        assert row.baseline_sha256 == result.baseline_sha256
        expected_diff = row.diff
    detail = service.get_task_file_edit_proposal(
        **{key: args[key] for key in ("user_id", "workspace_id", "task_id")},
        proposal_id=result.proposal_id,
    )
    assert detail.diff == expected_diff
    assert detail.status == "pending"
    assert not {"bound_root", "proposed_content"} & asdict(detail).keys()
    assert file.read_bytes() == original


@pytest.mark.parametrize("patch", [
    "invalid", make_patch("wrong\n", "new\n"),
    make_patch("old\n", "new\n").replace("src/中文 file.txt", "other.txt"),
])
def test_bad_patch_saves_nothing(patch_setup, engine, patch):
    args, file, sessions, _ = patch_setup
    with pytest.raises(UnifiedPatchError):
        service.create_task_file_patch_proposal(**{**args, "patch": patch})
    assert existing.count(engine) == 0
    assert len(sessions) == 1  # 预览失败不会开启保存事务。
    assert file.read_bytes() == b"old\n"


@pytest.mark.parametrize("kind", ["foreign", "missing", "unbound"])
def test_scope_rejection(patch_setup, engine, target, monkeypatch, kind):
    # 同一保存入口的既有归属失败契约也必须约束新入口。
    monkeypatch.setattr(service, "create_task_file_edit_proposal", service.create_task_file_patch_proposal)
    existing.test_initial_rejection_saves_nothing(patch_setup, engine, target, kind)


@pytest.mark.parametrize("kind", ["owner", "conversation", "binding", "deleted"])
def test_reauthorize_after_preview(patch_setup, engine, target, monkeypatch, kind):
    args, file, sessions, _ = patch_setup
    original_preview = service.preview_task_file_patch

    def preview(**kwargs):
        assert len(sessions) == 1 and sessions[0].closed
        result = original_preview(**kwargs)
        # 文件访问已结束，在独立事务模拟并发资源变更。
        with Session(engine) as session, session.begin():
            if kind == "owner":
                session.scalar(select(existing.Workspace)).user_id = target["other_id"]
            elif kind == "conversation":
                session.get(existing.Conversation, target["conversation_pk"]).user_id = target["other_id"]
            elif kind == "binding":
                session.scalar(select(existing.Workspace)).root_path = "/changed"
        if kind == "deleted":
            with Session(engine) as session:
                existing.delete_workspace_task(session, **{
                    key: args[key] for key in ("user_id", "workspace_id", "task_id")
                })
        return result

    monkeypatch.setattr(service, "preview_task_file_patch", preview)
    expected = service.ProposalBindingChangedError if kind == "binding" else existing.WorkspaceNotAccessibleError
    with pytest.raises(expected):
        service.create_task_file_patch_proposal(**args)
    assert existing.count(engine) == 0 and file.read_bytes() == b"old\n"


def test_external_edit_does_not_replace_snapshot(patch_setup, engine, monkeypatch):
    args, file, _, _ = patch_setup
    original = service.preview_task_file_patch

    def preview(**kwargs):
        result = original(**kwargs)
        file.write_bytes(b"external editor")
        return result

    monkeypatch.setattr(service, "preview_task_file_patch", preview)
    result = service.create_task_file_patch_proposal(**args)
    assert result.baseline_sha256 == sha256(b"old\n").hexdigest()
    with Session(engine) as session:
        assert session.scalar(select(FileEditProposal)).proposed_content == "new\n"
    assert file.read_bytes() == b"external editor"


@pytest.mark.parametrize("stage", ["after-flush", "before-commit", "after-commit"])
def test_transaction_failure_never_returns_success_or_retries(patch_setup, engine, monkeypatch, stage):
    args, file, _, session_class = patch_setup
    insert = service.insert_file_edit_proposal
    calls = []

    def fail(*args, **kwargs):
        raise RuntimeError("injected confirmation failure")

    def inserted(*args, **kwargs):
        calls.append(1)
        result = insert(*args, **kwargs)
        if stage == "after-flush":
            fail()
        return result

    monkeypatch.setattr(service, "insert_file_edit_proposal", inserted)
    event_name = stage.replace("-", "_")
    if stage != "after-flush":
        event.listen(session_class, event_name, fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            service.create_task_file_patch_proposal(**args)
    finally:
        if stage != "after-flush":
            event.remove(session_class, event_name, fail)
    # after_commit 已真实持久化，但调用方未收到回执，绝不能称为未保存。
    assert existing.count(engine) == (1 if stage == "after-commit" else 0)
    assert calls == [1] and file.read_bytes() == b"old\n"


def test_patch_uses_same_delete_lock_order(patch_setup, database, monkeypatch):
    monkeypatch.setattr(service, "create_task_file_edit_proposal", service.create_task_file_patch_proposal)
    existing.test_write_lock_order_matches_delete(patch_setup, database)
