"""扫描规则与真实受限文件I/O；实际授权由独立PostgreSQL API专项覆盖。"""

from dataclasses import asdict, replace
from hashlib import sha256
import os

import pytest

from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.files import (
    code_inventory as service,
    workspace_file,
    workspace_listing,
)
from app.services.workspace.files.code_ignore import CodeIgnoreError
from app.services.workspace.files.workspace_file import WorkspaceFileError


@pytest.fixture
def root(tmp_path, monkeypatch):
    project = tmp_path.resolve() / "project"
    project.mkdir()

    def resolve(**kwargs):
        assert kwargs["require_direct_path"] is True
        assert kwargs.get("expected_bound_root") in {None, str(project)}
        path = project / kwargs["relative_path"]
        if not os.path.lexists(path):
            raise WorkspacePathError("workspace_path_not_found", "目标不存在")
        return path

    for module in (service, workspace_file, workspace_listing):
        monkeypatch.setattr(module, "resolve_task_workspace_path", resolve)
    return project


def scan():
    return service.scan_code_inventory(
        user_id=1, workspace_id="a" * 32, task_id="b" * 32
    )


def write(root, path, content="print('中文')\r\n"):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8", newline="")
    return file


def test_typed_sorted_metadata_exact_bytes_and_no_writes(root, monkeypatch):
    write(root, "src/中文.py")
    write(root, "client.TSX", "const value = 1;\n")
    write(root, "pyproject.toml", '[project]\nname = "demo"\n')
    write(root, "Dockerfile", "FROM python:3.12\n")
    write(root, "README.md", "documentation")
    before = {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_ino,
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }
    result = scan()
    assert (
        result.source == "authorized_code_inventory"
        and result.policy == "gitignore_subset_v1"
    )
    assert result.workspace_id == "a" * 32 and result.task_id == "b" * 32
    assert result.scanned_directories == 2 and result.inspected_files == 4
    assert not result.truncated and result.incomplete_reasons == ()
    assert [
        (file.relative_path, file.file_type, file.language) for file in result.files
    ] == [
        ("Dockerfile", "configuration", "dockerfile"),
        ("client.TSX", "source", "typescript"),
        ("pyproject.toml", "configuration", "toml"),
        ("src/中文.py", "source", "python"),
    ]
    for file in result.files:
        data = (root / file.relative_path).read_bytes()
        assert file.byte_count == len(data) and file.sha256 == sha256(data).hexdigest()
    assert "content" not in repr(asdict(result)) and str(root) not in repr(
        asdict(result)
    )
    after = {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_ino,
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_hard_exclusions_cannot_be_restored_and_never_opened(root, monkeypatch):
    write(
        root,
        ".gitignore",
        "!node_modules/\n!dist/\n!.hidden.py\n!secrets.py\n!linked.py\n",
    )
    for path in (
        "node_modules/a.py",
        "dist/a.py",
        ".git/a.py",
        ".hidden.py",
        "secrets.py",
        "pnpm-lock.yaml",
    ):
        write(root, path, "PRIVATE")
    write(root, "safe.py")
    (root / "linked.py").symlink_to(root / "safe.py")
    (root / "linked-dir").symlink_to(root / "node_modules", target_is_directory=True)
    os.mkfifo(root / "pipe.py")
    original = service.read_task_text_file
    opened = []

    def read(**kwargs):
        opened.append(kwargs["relative_path"])
        return original(**kwargs)

    monkeypatch.setattr(service, "read_task_text_file", read)
    result = scan()
    assert [file.relative_path for file in result.files] == ["safe.py"]
    assert result.inspected_files == 1 and not result.truncated
    assert set(opened) == {".gitignore", "safe.py"}
    assert result.excluded_counts["link_or_special"] == 3
    assert result.excluded_counts["sensitive_name"] == 1


def test_nested_rules_last_match_and_ignored_parent_pruning(root, monkeypatch):
    write(root, ".gitignore", "*.py\n!src/\nparent/\n!parent/safe.py\n")
    write(root, "src/.gitignore", "!keep.py\n")
    write(root, "src/keep.py")
    write(root, "src/other.py")
    write(root, "root.py")
    write(root, "parent/safe.py")
    write(root, "parent/.gitignore", "UNSUPPORTED[\n")
    visited = []
    original = service.list_task_directory

    def listing(**kwargs):
        visited.append(kwargs["relative_path"])
        return original(**kwargs)

    monkeypatch.setattr(service, "list_task_directory", listing)
    result = scan()
    assert [file.relative_path for file in result.files] == ["src/keep.py"]
    assert visited == [".", "src"] and result.excluded_counts["gitignore"] == 3


def test_ignore_is_read_even_when_bounded_listing_omits_it(root, monkeypatch):
    write(root, ".gitignore", "blocked.py\n")
    write(root, "blocked.py", "PRIVATE")
    write(root, "safe.py")
    original = service.list_task_directory

    def listing(**kwargs):
        result = original(**kwargs)
        return replace(
            result,
            entries=tuple(
                entry for entry in result.entries if entry.name != ".gitignore"
            ),
            truncated=True,
        )

    monkeypatch.setattr(service, "list_task_directory", listing)
    result = scan()
    assert [file.relative_path for file in result.files] == ["safe.py"]
    assert result.incomplete_reasons == ("directory_entries",) and result.truncated


@pytest.mark.parametrize(
    "content",
    [
        'api_key = "private-value"',
        '{"password": "private-value"}',
        'token: "private-value"',
        "-----BEGIN RSA PRIVATE KEY-----",
        "sk-" + "a" * 24,
        "AKIA" + "A" * 16,
    ],
)
def test_obvious_credentials_are_not_candidates(root, content):
    write(root, "config.py", content)
    result = scan()
    assert result.files == () and result.inspected_files == 1
    assert result.excluded_counts == {"suspicious_content": 1}
    assert not result.truncated


@pytest.mark.parametrize("data", [b"\xff", b"a\x00b", b"x" * 262145])
def test_non_text_or_oversized_candidates_are_explicit_exclusions(root, data):
    (root / "file.py").write_bytes(data)
    result = scan()
    assert not result.files and result.excluded_counts == {"non_text_or_oversized": 1}
    assert result.inspected_files == 1 and not result.truncated


@pytest.mark.parametrize(
    "data", [b"[", b"\xff", b"a\x00b", b"x" * 16385, b"x" * 262145]
)
def test_bad_ignore_file_fails_entire_scan(root, data):
    (root / ".gitignore").write_bytes(data)
    write(root, "safe.py")
    with pytest.raises((CodeIgnoreError, WorkspaceFileError)):
        scan()


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_special_ignore_file_fails_without_following_or_blocking(root, kind):
    control = root / ".gitignore"
    if kind == "symlink":
        outside = root.parent / "external"
        outside.write_text("PRIVATE")
        control.symlink_to(outside)
    elif kind == "directory":
        control.mkdir()
    else:
        os.mkfifo(control)
    with pytest.raises(service.CodeInventoryError) as caught:
        scan()
    assert caught.value.code == "code_ignore_unavailable"


@pytest.mark.parametrize("change", ["created", "edited", "removed"])
def test_observed_policy_change_discards_prior_candidates(root, monkeypatch, change):
    control = root / ".gitignore"
    if change != "created":
        control.write_text("other.py\n")
    write(root, "safe.py")
    original = service.read_task_text_file

    def read(**kwargs):
        result = original(**kwargs)
        if kwargs["relative_path"] == "safe.py":
            if change == "removed":
                control.unlink()
            else:
                control.write_text("safe.py\n")
        return result

    monkeypatch.setattr(service, "read_task_text_file", read)
    with pytest.raises(service.CodeInventoryError) as caught:
        scan()
    assert caught.value.code == "code_ignore_changed"


@pytest.mark.parametrize("count", [20, 21])
def test_file_budget_requires_an_extra_eligible_candidate(root, count):
    for index in range(count):
        write(root, f"{index:02d}.py")
    result = scan()
    assert result.inspected_files == len(result.files) == 20
    assert result.truncated is (count == 21)
    assert result.incomplete_reasons == (("file_budget",) if count == 21 else ())


def test_failed_text_candidates_also_consume_read_budget(root):
    for index in range(21):
        (root / f"{index:02d}.py").write_bytes(b"\x00")
    result = scan()
    assert result.files == () and result.inspected_files == 20 and result.truncated
    assert result.excluded_counts == {"non_text_or_oversized": 20}


@pytest.mark.parametrize(
    "budget,expected",
    [("MAX_CODE_DIRECTORIES", "directory_budget"), ("MAX_CODE_DEPTH", "depth_budget")],
)
def test_directory_and_depth_coverage_are_bounded(root, monkeypatch, budget, expected):
    monkeypatch.setattr(service, budget, 1)
    write(root, "a/deep/a.py")
    write(root, "b/b.py")
    result = scan()
    assert result.truncated and expected in result.incomplete_reasons
    assert result.scanned_directories <= (1 if budget == "MAX_CODE_DIRECTORIES" else 3)


def test_unsupported_entry_path_is_a_coverage_gap(root):
    write(root, "bad:name.py")
    result = scan()
    assert (
        not result.files
        and result.truncated
        and result.incomplete_reasons == ("unsupported_path",)
    )


@pytest.mark.parametrize(
    "code", ["file_not_found", "file_changed", "file_access_denied", "file_unavailable"]
)
def test_unexpected_read_failure_is_not_partial_success(root, monkeypatch, code):
    write(root, "a.py")
    write(root, "b.py")
    original = service.read_task_text_file

    def read(**kwargs):
        if kwargs["relative_path"] == "b.py":
            raise WorkspaceFileError(code, "读取失败")
        return original(**kwargs)

    monkeypatch.setattr(service, "read_task_text_file", read)
    with pytest.raises(WorkspaceFileError) as caught:
        scan()
    assert caught.value.code == code


def test_complete_empty_inventory(root):
    result = scan()
    assert result.files == () and result.inspected_files == 0
    assert result.scanned_directories == 1 and not result.truncated
