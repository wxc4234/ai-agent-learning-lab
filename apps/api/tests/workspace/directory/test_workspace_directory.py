"""真实临时目录覆盖路径语义；故障注入覆盖不依赖系统用户权限的异常。"""

from pathlib import Path

import pytest

from app.services.workspace.directory.workspace_directory import (
    WorkspaceDirectoryError,
    validate_workspace_directory,
)


def reject(value, code):
    with pytest.raises(WorkspaceDirectoryError) as caught:
        validate_workspace_directory(value)
    assert caught.value.code == code
    assert isinstance(caught.value, ValueError)
    return caught.value


@pytest.mark.parametrize("value", [None, 123, b"/tmp", Path("/tmp"), "", "\x00", "relative", "../project", "~/project", "$HOME/project"])
def test_invalid_input(value):
    reject(value, "invalid_directory_path")


def test_real_directory_preserves_unicode_spaces_and_contents(tmp_path):
    directory = tmp_path / " 中文 项目 "
    directory.mkdir()
    source = directory / "main.py"
    source.write_text("print('hello')\n")
    before = source.stat()
    result = validate_workspace_directory(str(directory))
    assert result == directory.resolve(strict=True)
    assert result.name == " 中文 项目 "
    assert list(directory.iterdir()) == [source]
    assert source.read_text() == "print('hello')\n"
    assert source.stat().st_mtime_ns == before.st_mtime_ns


def test_parent_segments_are_resolved_on_filesystem(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    assert validate_workspace_directory(str(child / "..")) == tmp_path.resolve()


def test_missing_directory_is_not_created(tmp_path):
    missing = tmp_path / "missing" / "project"
    reject(str(missing), "directory_not_found")
    assert not (tmp_path / "missing").exists()


def test_file_and_file_as_parent(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("content")
    reject(str(source), "not_a_directory")
    reject(str(source / "child"), "directory_not_found")


def test_directory_link_returns_target_and_keeps_link(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert validate_workspace_directory(str(link)) == target.resolve()
    assert link.is_symlink()


def test_symlink_parent_is_resolved_before_dot_dot(tmp_path):
    target = tmp_path / "actual" / "project"
    target.mkdir(parents=True)
    link = tmp_path / "entry"
    link.symlink_to(target, target_is_directory=True)
    assert validate_workspace_directory(str(link / "..")) == target.parent.resolve()


@pytest.mark.parametrize("kind", ["broken", "cycle", "file", "root"])
def test_rejected_links(tmp_path, kind):
    link = tmp_path / "link"
    if kind == "broken":
        target = tmp_path / "missing"
        code = "directory_not_found"
    elif kind == "cycle":
        target = link
        code = "invalid_directory_path"
    elif kind == "file":
        target = tmp_path / "file"
        target.write_text("content")
        code = "not_a_directory"
    else:
        target = Path(tmp_path.anchor)
        code = "root_directory_not_allowed"
    link.symlink_to(target, target_is_directory=kind == "root")
    reject(str(link), code)


def test_filesystem_root_rejected(tmp_path):
    reject(tmp_path.anchor, "root_directory_not_allowed")


@pytest.mark.parametrize("operation", ["resolve", "stat"])
@pytest.mark.parametrize("error,code", [
    (FileNotFoundError, "directory_not_found"),
    (NotADirectoryError, "directory_not_found"),
    (PermissionError, "directory_access_denied"),
    (OSError, "directory_unavailable"),
])
def test_filesystem_failures_are_classified_without_raw_details(tmp_path, monkeypatch, operation, error, code):
    # 单独模拟 resolve 之后的 stat 故障，包含检查过程中目录消失的情况。
    raw = str(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", lambda self, *, strict: self)

        def fail(self, **kwargs):
            raise error("private-path-and-internal-details")

        patch.setattr(Path, operation, fail)
        caught = reject(raw, code)
    assert "private-path-and-internal-details" not in str(caught)
    assert caught.__suppress_context__
