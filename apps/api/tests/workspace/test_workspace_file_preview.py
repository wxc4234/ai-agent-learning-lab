"""授权文件预览：单次读取基线、异常传播与真实只读边界。"""

import asyncio
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace import workspace_file_preview as service
from app.services.workspace.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.workspace_edit_preview import EditPreviewError
from app.services.workspace.workspace_file import WorkspaceFileError, WorkspaceTextFile
from app.services.workspace.workspace_path import WorkspacePathError
from tests.workspace import test_workspace_path as path_tests


root = path_tests.root
target = path_tests.target
database = path_tests.database


def preview(**overrides):
    return service.preview_task_file_replacement(**{
        "user_id": 1, "workspace_id": "w", "task_id": "t",
        "relative_path": "./src//file.txt", "old_text": "old", "new_text": "new", **overrides,
    })


@pytest.mark.parametrize("content", ["old", "old\n", "old\r", "old\r\n", "\ufeffold\r\n", "中文😀old", "a\r\nb\rold\n"])
def test_single_read_and_exact_utf8_baseline(monkeypatch, content):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        assert len(calls) == 1
        return WorkspaceTextFile("src/file.txt", content, len(content.encode("utf-8")))

    monkeypatch.setattr(service, "read_task_text_file", read)
    result = preview()
    assert calls == [{"user_id": 1, "workspace_id": "w", "task_id": "t", "relative_path": "./src//file.txt"}]
    assert result.relative_path == "src/file.txt"
    assert result.baseline_sha256 == sha256(content.encode("utf-8")).hexdigest()
    assert result.baseline_sha256 != sha256(result.preview.updated_content.encode("utf-8")).hexdigest()
    assert result.preview.updated_content == content.replace("old", "new")
    assert result.preview.before_byte_count == len(content.encode("utf-8"))
    with pytest.raises(FrozenInstanceError):
        result.baseline_sha256 = "changed"
    with pytest.raises(FrozenInstanceError):
        result.preview.updated_content = "changed"


@pytest.mark.parametrize("error", [
    WorkspaceNotAccessibleError(), WorkspaceDirectoryError("directory_not_found", "safe"),
    WorkspacePathError("workspace_directory_unbound", "safe"),
    WorkspaceFileError("file_changed", "safe"), RuntimeError("internal"), asyncio.CancelledError(),
])
def test_read_failure_propagates_without_preview(monkeypatch, error):
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(service, "read_task_text_file", fail)
    monkeypatch.setattr(service, "preview_text_replacement", lambda **kwargs: pytest.fail("must not preview"))
    with pytest.raises(type(error)) as caught:
        preview()
    assert caught.value is error


@pytest.mark.parametrize("content,old,new,code", [
    ("old", "", "new", "empty_old_text"),
    ("old", "old", "old", "edit_no_change"),
    ("old", "absent", "new", "edit_target_not_found"),
    ("old old", "old", "new", "edit_target_ambiguous"),
])
def test_preview_failure_propagates_without_digest(monkeypatch, content, old, new, code):
    monkeypatch.setattr(service, "read_task_text_file", lambda **kwargs: WorkspaceTextFile("file", content, len(content)))
    monkeypatch.setattr(service, "sha256", lambda *args: pytest.fail("no successful result on preview failure"))
    with pytest.raises(EditPreviewError) as caught:
        preview(old_text=old, new_text=new)
    assert caught.value.code == code


def test_truncated_diff_keeps_complete_result_and_original_digest(monkeypatch):
    content = "a" * 20000
    monkeypatch.setattr(service, "read_task_text_file", lambda **kwargs: WorkspaceTextFile("file", content, len(content)))
    result = preview(old_text=content, new_text="b" * 20000)
    assert result.preview.diff_truncated
    assert result.preview.updated_content == "b" * 20000
    assert result.baseline_sha256 == sha256(content.encode()).hexdigest()


@pytest.mark.parametrize("content", [b"old\r\n", b"\xef\xbb\xbfold\n", "中文old😀".encode()])
def test_authorized_real_file_stays_unchanged_and_session_closed(database, target, root, monkeypatch, content):
    path = root / "src" / "preview.txt"
    path.write_bytes(content)
    original = service.preview_text_replacement

    def generate(**kwargs):
        assert database[0] and all(session.closed for session in database[0])
        assert all(not session.in_transaction() for session in database[0])
        return original(**kwargs)

    monkeypatch.setattr(service, "preview_text_replacement", generate)
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    result = preview(**args, relative_path="./src//preview.txt")
    assert path.read_bytes() == content
    assert result.relative_path == "src/preview.txt"
    assert result.baseline_sha256 == sha256(content).hexdigest()
    assert result.preview.updated_content.encode() == content.replace(b"old", b"new")
    assert database[1] and all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])


def test_file_changes_after_read_do_not_mix_baseline(database, target, root, monkeypatch):
    path = root / "src" / "preview.txt"
    path.write_bytes(b"old\r\n")
    original = service.preview_text_replacement

    def change_after_read(**kwargs):
        # 模拟另一编辑器在读取完成后修改文件；预览仍应基于同一份旧内容。
        path.write_bytes(b"external change")
        return original(**kwargs)

    monkeypatch.setattr(service, "preview_text_replacement", change_after_read)
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    result = preview(**args, relative_path="src/preview.txt")
    assert result.baseline_sha256 == sha256(b"old\r\n").hexdigest()
    assert result.preview.updated_content == "new\r\n"
    assert path.read_bytes() == b"external change"


@pytest.mark.parametrize("kind", ["foreign", "unbound", "outside", "missing", "invalid-utf8", "ambiguous"])
def test_real_rejection_preserves_file(engine, database, target, root, monkeypatch, kind):
    path = root / "src" / "preview.txt"
    original_bytes = b"\xff" if kind == "invalid-utf8" else b"old old" if kind == "ambiguous" else b"old"
    path.write_bytes(original_bytes)
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    relative = "src/preview.txt"
    expected = WorkspacePathError
    if kind == "foreign":
        args["user_id"] = target["other_id"]
        expected = WorkspaceNotAccessibleError
    elif kind == "unbound":
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = None
    elif kind == "outside":
        (root / "link").symlink_to(root.parent, target_is_directory=True)
        relative = "link"
    elif kind == "missing":
        relative = "missing"
    elif kind == "invalid-utf8":
        expected = WorkspaceFileError
    else:
        expected = EditPreviewError
    if kind != "ambiguous":
        monkeypatch.setattr(service, "preview_text_replacement", lambda **kwargs: pytest.fail("read/authorization must reject first"))
    with pytest.raises(expected):
        preview(**args, relative_path=relative)
    assert path.read_bytes() == original_bytes
