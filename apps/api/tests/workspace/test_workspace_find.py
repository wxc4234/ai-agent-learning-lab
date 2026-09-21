"""跨目录文件查找：真实目录边界、失败清理及授权事务。"""

import errno
import os
from dataclasses import FrozenInstanceError
from pathlib import Path, PurePosixPath

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace import workspace_find as service
from app.services.workspace.workspace_path import WorkspacePathError
from tests.workspace import test_workspace_listing as listing_tests
from tests.workspace import test_workspace_path as path_tests


# 复用真实描述符/迭代器清理跟踪和根 PostgreSQL 隔离夹具。
resources = listing_tests.resources
root = path_tests.root
target = path_tests.target
database = path_tests.database


@pytest.fixture
def directory(tmp_path, monkeypatch):
    path = tmp_path.resolve() / "project"
    path.mkdir()
    monkeypatch.setattr(service, "resolve_task_workspace_path", lambda **kwargs: path)
    return path


def find(query="hit", **kwargs):
    return service.find_task_files(user_id=1, workspace_id="w", task_id="t", query=query, **kwargs)


@pytest.mark.parametrize("query", [None, 1, True, "", "x" * 129, "a/b", "a\\b", "a\n", "a\r", "a\x00"])
def test_query_rejected_before_io(monkeypatch, query):
    monkeypatch.setattr(service, "resolve_task_workspace_path", lambda **kwargs: pytest.fail("no I/O"))
    with pytest.raises(service.WorkspaceFindError) as caught:
        find(query)
    assert caught.value.code == "invalid_find_query"


@pytest.mark.parametrize("query", [" ", "中文", "*", "[ab]", "x" * 128])
def test_query_is_literal_and_preserves_characters(directory, resources, query):
    (directory / query).touch()
    result = find(query)
    assert result.paths == (query,)
    assert result.scanned_entries == 1
    assert result.truncated is False


def test_nested_basename_only_sorted_and_independent(directory, resources, monkeypatch):
    (directory / "runtime").mkdir()
    (directory / "runtime" / "executor.py").touch()
    (directory / "tests").mkdir()
    (directory / "tests" / "test_runtime.py").touch()
    (directory / "agent_runtime.py").touch()
    (directory / "RUNTIME.py").touch()
    (directory / ".runtime").touch()
    monkeypatch.setattr(os, "read", lambda *args: pytest.fail("no content read"))
    result = find("runtime", relative_path="./src//")
    assert result.paths == ("src/.runtime", "src/agent_runtime.py", "src/tests/test_runtime.py")
    assert result.relative_path == "src"
    assert result.scanned_entries == 7
    assert not result.truncated
    assert find("missing").paths == ()
    assert find("missing").scanned_entries == 7
    with pytest.raises(FrozenInstanceError):
        result.truncated = True


@pytest.mark.parametrize("count", [0, 1, 50, 51, 70])
def test_match_budget(directory, resources, count):
    for i in range(count):
        (directory / f"hit-{i:03d}").touch()
    result = find()
    assert len(result.paths) == min(count, 50)
    assert result.truncated is (count > 50)
    assert result.scanned_entries == min(count, 51)
    assert sum(scan.count for scan in resources) == min(count, 51)
    assert result.paths == tuple(sorted(result.paths))


@pytest.mark.parametrize("count", [2000, 2001, 2050])
def test_scan_budget_without_matches(directory, resources, count):
    for i in range(count):
        (directory / f"item-{i}").touch()
    result = find()
    assert result.paths == ()
    assert result.scanned_entries == 2000
    assert result.truncated is (count > 2000)
    assert sum(scan.count for scan in resources) == min(count, 2001)


def test_budget_shared_across_subdirectories(directory, resources, monkeypatch):
    monkeypatch.setattr(service, "MAX_FIND_ENTRIES", 5)
    for name in ["a", "b", "c"]:
        child = directory / name
        child.mkdir()
        for i in range(3):
            (child / f"file-{i}").touch()
    result = find()
    assert result.scanned_entries == 5
    assert result.truncated
    assert sum(scan.count for scan in resources) == 6


@pytest.mark.parametrize("depth", [8, 9])
def test_depth_boundary_and_sibling_continues(directory, resources, depth):
    current = directory
    for _ in range(depth):
        current = current / "d"
        current.mkdir()
    (current / "hit").touch()
    (directory / "hit-root").touch()
    result = find()
    assert "hit-root" in result.paths
    assert ("/".join(["d"] * depth + ["hit"]) in result.paths) is (depth == 8)
    assert result.truncated is (depth == 9)
    assert len(resources) == 9


def test_path_limit_skips_branch_but_continues(directory, resources, monkeypatch):
    monkeypatch.setattr(service, "MAX_FIND_PATH_CHARACTERS", 8)
    (directory / "long-directory").mkdir()
    (directory / "long-directory" / "hit").touch()
    (directory / "hit-long-name").touch()
    (directory / "hit").touch()
    result = find()
    assert result.paths == ("hit",)
    assert result.truncated
    assert len(resources) == 1


def test_no_follow_links_or_open_special_files(directory, resources, monkeypatch):
    (directory / "real").mkdir()
    (directory / "real" / "hit").touch()
    (directory / "hit-file-link").symlink_to(directory / "real" / "hit")
    (directory / "hit-dir-link").symlink_to(directory / "real", target_is_directory=True)
    (directory / "hit-outside").symlink_to(directory.parent, target_is_directory=True)
    (directory / "hit-broken").symlink_to(directory / "absent")
    os.mkfifo(directory / "hit-pipe")
    monkeypatch.setattr(os, "readlink", lambda *args, **kwargs: pytest.fail("no link target read"))
    assert find().paths == ("real/hit",)
    assert len(resources) == 2


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_child_replaced_before_open(directory, resources, monkeypatch, kind):
    child = directory / "child"
    child.mkdir()
    original = os.open

    def replaced(name, flags, *args, **kwargs):
        if name == "child":
            child.rename(directory / "old")
            if kind == "symlink":
                child.symlink_to(directory.parent, target_is_directory=True)
            else:
                child.mkdir()
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replaced)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {replaced})
    with pytest.raises(service.WorkspaceFindError) as caught:
        find()
    assert caught.value.code in {"file_find_changed", "file_find_unavailable"}
    assert len(resources) == 1


def test_child_disappears(directory, resources, monkeypatch):
    (directory / "hit").touch()
    original = os.DirEntry.stat

    def vanished(entry, *, follow_symlinks=True):
        assert follow_symlinks is False
        (directory / entry.name).unlink()
        return original(entry, follow_symlinks=False)

    monkeypatch.setattr(os.DirEntry, "stat", vanished)
    with pytest.raises(service.WorkspaceFindError, match="发生变化"):
        find()


def test_directory_changes_during_scan(directory, resources, monkeypatch):
    (directory / "hit").touch()
    original = os.DirEntry.stat

    def changed(entry, *, follow_symlinks=True):
        metadata = original(entry, follow_symlinks=follow_symlinks)
        os.utime(directory, ns=(1_000_000_000, 2_000_000_000))
        return metadata

    monkeypatch.setattr(os.DirEntry, "stat", changed)
    with pytest.raises(service.WorkspaceFindError, match="发生变化"):
        find()


@pytest.mark.parametrize("stage", ["open", "scan", "stat"])
@pytest.mark.parametrize("error,code", [
    (PermissionError("PRIVATE"), "file_find_access_denied"),
    (OSError(errno.EIO, "PRIVATE"), "file_find_unavailable"),
])
def test_failure_closes_resources_and_hides_paths(directory, resources, monkeypatch, stage, error, code):
    child = directory / "child"
    child.mkdir()
    (child / "hit").touch()
    original_open, original_scan = os.open, os.scandir
    child_inode = child.stat().st_ino

    def fail_open(name, *args, **kwargs):
        if name == "child":
            raise error
        return original_open(name, *args, **kwargs)

    def fail_scan(fd):
        if os.fstat(fd).st_ino == child_inode:
            raise error
        return original_scan(fd)

    def fail_stat(*args, **kwargs):
        raise error

    if stage == "open":
        monkeypatch.setattr(os, "open", fail_open)
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {fail_open})
    elif stage == "scan":
        monkeypatch.setattr(os, "scandir", fail_scan)
        monkeypatch.setattr(os, "supports_fd", os.supports_fd | {fail_scan})
    else:
        monkeypatch.setattr(os.DirEntry, "stat", fail_stat)
    with pytest.raises(service.WorkspaceFindError) as caught:
        find()
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("capability", ["open", "scan", "directory_flag", "nofollow_flag"])
def test_platform_refuses_fallback(directory, monkeypatch, capability):
    if capability == "open":
        monkeypatch.setattr(os, "supports_dir_fd", set())
    elif capability == "scan":
        monkeypatch.setattr(os, "supports_fd", set())
    else:
        monkeypatch.delattr(os, "O_DIRECTORY" if capability == "directory_flag" else "O_NOFOLLOW")
    monkeypatch.setattr(service, "_find_resolved_directory", lambda *args, **kwargs: pytest.fail("no fallback"))
    with pytest.raises(service.WorkspaceFindError) as caught:
        find()
    assert caught.value.code == "file_find_unsupported"


@pytest.mark.parametrize("path", [Path("relative"), Path("/a/../b")])
def test_internal_path_guard(path):
    with pytest.raises(service.WorkspaceFindError):
        service._find_resolved_directory(path, relative_path=PurePosixPath("."), query="hit")


def test_authorized_find_closes_session_before_scan(database, target, root, monkeypatch):
    original = service._find_resolved_directory

    def scan(path, **kwargs):
        assert database[0] and all(session.closed for session in database[0])
        return original(path, **kwargs)

    monkeypatch.setattr(service, "_find_resolved_directory", scan)
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    result = service.find_task_files(**args, query="中文")
    assert result.paths == ("src/中文 file.txt",)
    assert not result.truncated
    assert database[1] and all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])


@pytest.mark.parametrize("kind", ["foreign", "unbound", "outside", "parent"])
def test_authorization_and_path_checks_precede_scan(engine, database, target, root, monkeypatch, kind):
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    relative = "."
    if kind == "foreign":
        args["user_id"] = target["other_id"]
    elif kind == "unbound":
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = None
    elif kind == "outside":
        (root / "link").symlink_to(root.parent, target_is_directory=True)
        relative = "link"
    else:
        relative = "../"
    monkeypatch.setattr(service, "_find_resolved_directory", lambda *args, **kwargs: pytest.fail("must not scan"))
    expected = WorkspaceNotAccessibleError if kind == "foreign" else WorkspacePathError
    with pytest.raises(expected):
        service.find_task_files(**args, relative_path=relative, query="hit")


@pytest.mark.parametrize("kind", ["symlink", "file", "missing"])
def test_start_directory_replaced_before_open(directory, resources, kind):
    moved = directory.with_name("moved")
    directory.rename(moved)
    if kind == "symlink":
        directory.symlink_to(moved, target_is_directory=True)
    elif kind == "file":
        directory.touch()
    with pytest.raises(service.WorkspaceFindError):
        find()
    assert not resources


def test_opened_child_fstat_failure_still_closes_fd(directory, resources, monkeypatch):
    child = directory / "child"
    child.mkdir()
    child_inode = child.stat().st_ino
    original = os.fstat

    def fail(fd):
        metadata = original(fd)
        if metadata.st_ino == child_inode:
            raise OSError(errno.EIO, "PRIVATE")
        return metadata

    monkeypatch.setattr(os, "fstat", fail)
    with pytest.raises(service.WorkspaceFindError) as caught:
        find()
    assert caught.value.code == "file_find_unavailable"
    assert len(resources) == 1


def test_exact_path_length_is_not_truncated(directory, resources, monkeypatch):
    monkeypatch.setattr(service, "MAX_FIND_PATH_CHARACTERS", 3)
    (directory / "hit").touch()
    result = find()
    assert result.paths == ("hit",)
    assert not result.truncated
