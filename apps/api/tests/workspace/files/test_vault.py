"""Vault 有界清单与来源：真实临时文件，授权联表由 API 专项验证。"""

import os
from hashlib import sha256

import pytest

from app.services.workspace.files import vault, workspace_file, workspace_listing


@pytest.fixture
def notes(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "vault"
    root.mkdir()

    def resolved(
        *, user_id, workspace_id, task_id, relative_path,
        require_direct_path=False, expected_bound_root=None,
    ):
        # 仅替换身份/路径装配；枚举和读取仍使用真实无跟随描述符。
        # 严格解析与真实授权在隔离 PostgreSQL API 测试中覆盖。
        assert require_direct_path is True
        return root / relative_path

    monkeypatch.setattr(workspace_file, "resolve_task_workspace_path", resolved)
    monkeypatch.setattr(workspace_listing, "resolve_task_workspace_path", resolved)
    return root


def listing():
    return vault.list_vault_markdown(
        user_id=1, workspace_id="a" * 32, task_id="b" * 32,
    )


def document(path: str):
    return vault.read_vault_markdown(
        user_id=1, workspace_id="a" * 32, task_id="b" * 32,
        relative_path=path,
    )


def test_nested_markdown_only_and_no_content_reads(notes, monkeypatch):
    (notes / "README.md").write_text("root")
    nested = notes / "笔记"
    nested.mkdir()
    (nested / " Docker.MD").write_text("中文")
    (notes / "image.png").write_bytes(b"image")
    (notes / ".hidden.md").write_text("private")
    hidden = notes / ".obsidian"
    hidden.mkdir()
    (hidden / "config.md").write_text("private")
    (notes / "alias.md").symlink_to(notes / "README.md")
    (notes / "alias-dir").symlink_to(nested, target_is_directory=True)
    os.mkfifo(notes / "pipe.md")
    monkeypatch.setattr(os, "read", lambda *args: pytest.fail("listing read content"))

    result = listing()
    assert result.paths == ("README.md", "笔记/ Docker.MD")
    assert result.scanned_directories == 2 and not result.truncated
    assert not (notes / ".git").exists()


@pytest.mark.parametrize("only_attachment", [False, True])
def test_complete_empty_inventory(notes, only_attachment):
    if only_attachment:
        (notes / "image.png").touch()
    result = listing()
    assert result.paths == () and not result.truncated


@pytest.mark.parametrize("data", [b"", b"hello\r\nlast\n", "你好\n第二行".encode(), b"a" * 262144])
def test_source_matches_single_read_exact_bytes(notes, data):
    (notes / "中文.MD").write_bytes(data)
    result = document("./中文.MD")
    assert result.source.workspace_id == "a" * 32
    assert result.task_id == "b" * 32
    assert result.source.relative_path == "中文.MD"
    assert result.content.encode() == data
    assert result.byte_count == len(data)
    assert result.source.sha256 == sha256(data).hexdigest()
    assert result.source.start_line == (1 if data else None)
    assert result.source.end_line == (len(data.decode().splitlines()) if data else None)


@pytest.mark.parametrize("path,code", [
    ("../private.md", "invalid_vault_path"), ("notes/../private.md", "invalid_vault_path"),
    ("/etc/private.md", "invalid_vault_path"), ("C:/private.md", "invalid_vault_path"),
    ("C:private.md", "invalid_vault_path"), (r"notes\private.md", "invalid_vault_path"),
    ("", "invalid_vault_path"), ("note\x00.md", "invalid_vault_path"),
    ("notes/NUL.md", "invalid_vault_path"), ("file:stream.md", "invalid_vault_path"),
    ("bad\nname.md", "invalid_vault_path"), ("a" * 4097, "invalid_vault_path"),
    (".obsidian/config.md", "vault_path_excluded"), (".secret.md", "vault_path_excluded"),
    ("image.png", "vault_file_unsupported"), (".", "vault_file_unsupported"),
])
def test_invalid_paths_rejected_before_file_access(monkeypatch, path, code):
    monkeypatch.setattr(vault, "read_task_text_file", lambda **kwargs: pytest.fail("invalid read"))
    with pytest.raises(vault.VaultError) as caught:
        document(path)
    assert caught.value.code == code


@pytest.mark.parametrize("data,code", [
    (b"\xff", "file_not_utf8_text"), (b"a\x00b", "file_not_utf8_text"),
    (b"a" * 262145, "file_too_large"),
])
def test_unsupported_content_rejected(notes, data, code):
    (notes / "note.md").write_bytes(data)
    with pytest.raises(workspace_file.WorkspaceFileError) as caught:
        document("note.md")
    assert caught.value.code == code


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_markdown_rejected_without_reading(notes, monkeypatch, kind):
    target = notes / "note.md"
    if kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)
    monkeypatch.setattr(os, "read", lambda *args: pytest.fail("special file read"))
    with pytest.raises(workspace_file.WorkspaceFileError) as caught:
        document("note.md")
    assert caught.value.code == "file_not_regular"


@pytest.mark.parametrize("count", [100, 101])
def test_file_result_budget(notes, count):
    for number in range(count):
        (notes / f"{number:03d}.md").touch()
    result = listing()
    assert len(result.paths) == min(count, 100)
    assert result.truncated is (count > 100)


@pytest.mark.parametrize("count", [20, 21])
def test_directory_budget_includes_root_and_queue(notes, count):
    for number in range(count - 1):
        (notes / f"dir-{number:03d}").mkdir()
    result = listing()
    assert result.scanned_directories == min(count, 20)
    assert result.truncated is (count > 20)


@pytest.mark.parametrize("depth", [8, 9])
def test_depth_budget(notes, depth):
    current = notes
    for _ in range(depth):
        current = current / "next"
        current.mkdir()
    (current / "note.md").touch()
    result = listing()
    assert result.scanned_directories == min(depth + 1, 9)
    assert bool(result.paths) is (depth == 8)
    assert result.truncated is (depth > 8)


def test_entry_budget_reports_incomplete_even_without_markdown(notes):
    for number in range(201):
        (notes / f"{number:03d}.txt").touch()
    result = listing()
    assert result.paths == () and result.truncated


def test_unsupported_entry_name_marks_incomplete(notes):
    (notes / "invalid:name.md").touch()
    assert listing().truncated


def test_listing_is_not_permission_or_content_snapshot(notes):
    path = notes / "note.md"
    path.write_text("old")
    assert listing().paths == ("note.md",)
    old = document("note.md")
    path.write_text("new")
    new = document("note.md")
    assert new.content == "new" and new.source.sha256 != old.source.sha256
    path.unlink()
    with pytest.raises(workspace_file.WorkspaceFileError) as caught:
        document("note.md")
    assert caught.value.code == "file_not_found"


def test_scan_failure_cannot_become_empty_success(notes, monkeypatch):
    (notes / "child").mkdir()
    original = vault.list_task_directory

    def changed(**kwargs):
        if kwargs["relative_path"] == "child":
            raise workspace_listing.WorkspaceListingError(
                "directory_listing_changed", "目录内容在枚举期间发生变化，请重新查询",
            )
        return original(**kwargs)

    monkeypatch.setattr(vault, "list_task_directory", changed)
    with pytest.raises(workspace_listing.WorkspaceListingError):
        listing()
