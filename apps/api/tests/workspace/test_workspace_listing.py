"""限量目录枚举：真实目录、链接、资源清理与隔离授权。"""

import errno
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace import workspace_listing as service
from app.services.workspace.workspace_path import WorkspacePathError
from tests.workspace import test_workspace_path as path_tests


root = path_tests.root
target = path_tests.target
database = path_tests.database


@pytest.fixture
def directory(tmp_path, monkeypatch):
    path = tmp_path.resolve() / "project"
    path.mkdir()
    monkeypatch.setattr(service, "resolve_task_workspace_path", lambda **kwargs: path)
    return path


def listing():
    return service.list_task_directory(user_id=1, workspace_id="w", task_id="t")


@pytest.fixture
def resources(monkeypatch):
    original_open, original_close, original_scan = os.open, os.close, os.scandir
    active = set()
    scans = []

    def open_tracked(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        active.add(descriptor)
        return descriptor

    def close_tracked(descriptor):
        original_close(descriptor)
        active.remove(descriptor)

    class Scan:
        def __init__(self, descriptor):
            self.iterator = original_scan(descriptor)
            self.count = 0
            self.closed = False
            scans.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.iterator.close()
            self.closed = True

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self.iterator)
            self.count += 1
            return entry

    monkeypatch.setattr(os, "open", open_tracked)
    monkeypatch.setattr(os, "close", close_tracked)
    monkeypatch.setattr(os, "scandir", Scan)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {open_tracked})
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {Scan})
    yield scans
    assert not active
    assert all(scan.closed for scan in scans)


@pytest.mark.parametrize("count", [0, 1, 200, 201, 250])
def test_entry_limit_and_bounded_iteration(directory, resources, count):
    for index in range(count):
        (directory / f"item-{index:03d}").touch()
    result = listing()
    assert len(result.entries) == min(count, 200)
    assert result.truncated is (count > 200)
    assert resources[0].count == min(count, 201)
    names = [entry.name for entry in result.entries]
    assert names == sorted(names)
    assert result.relative_path == "."
    with pytest.raises(FrozenInstanceError):
        result.truncated = True


def test_real_kinds_hidden_names_and_no_recursive_or_link_read(directory, resources, monkeypatch):
    (directory / ".env").write_text("secret")
    (directory / "中文 file").touch()
    (directory / "nested").mkdir()
    (directory / "nested" / "child").touch()
    (directory / "internal").symlink_to(directory / "nested", target_is_directory=True)
    (directory / "external").symlink_to(directory.parent, target_is_directory=True)
    (directory / "broken").symlink_to(directory / "missing")
    os.mkfifo(directory / "pipe")
    monkeypatch.setattr(os, "read", lambda *args: pytest.fail("must not read content"))
    monkeypatch.setattr(os, "readlink", lambda *args, **kwargs: pytest.fail("must not read link target"))
    result = listing()
    assert {entry.name: entry.kind for entry in result.entries} == {
        ".env": "file", "中文 file": "file", "nested": "directory",
        "internal": "symlink", "external": "symlink", "broken": "symlink", "pipe": "other",
    }
    assert len(resources) == 1
    with pytest.raises(FrozenInstanceError):
        result.entries[0].name = "changed"


@pytest.mark.parametrize("stage", ["leaf", "ancestor"])
def test_directory_replaced_by_link_before_open(directory, resources, monkeypatch, stage):
    original_open = os.open
    target_path = directory
    if stage == "ancestor":
        child = directory / "nested"
        child.mkdir()
        monkeypatch.setattr(service, "resolve_task_workspace_path", lambda **kwargs: child)
    outside = directory.parent / ("outside-" + stage)
    outside.mkdir()
    changed = False

    def replaced(name, flags, *args, **kwargs):
        nonlocal changed
        if name == target_path.name and not changed:
            changed = True
            target_path.rename(target_path.with_name(target_path.name + "-moved"))
            target_path.symlink_to(outside, target_is_directory=True)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replaced)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {replaced})
    with pytest.raises(service.WorkspaceListingError) as caught:
        listing()
    assert changed and not resources
    assert caught.value.code in {"directory_listing_changed", "directory_listing_not_directory"}


def test_child_disappears_during_stat(directory, resources, monkeypatch):
    (directory / "vanish").touch()
    original = os.DirEntry.stat

    def disappear(entry, *, follow_symlinks=True):
        assert follow_symlinks is False
        (directory / entry.name).unlink()
        return original(entry, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os.DirEntry, "stat", disappear)
    with pytest.raises(service.WorkspaceListingError) as caught:
        listing()
    assert caught.value.code == "directory_listing_changed"


def test_directory_metadata_change_is_rejected(directory, resources, monkeypatch):
    (directory / "item").touch()
    original = os.DirEntry.stat

    def changed(entry, *, follow_symlinks=True):
        metadata = original(entry, follow_symlinks=follow_symlinks)
        os.utime(directory, ns=(1_000_000_000, 2_000_000_000))
        return metadata

    monkeypatch.setattr(os.DirEntry, "stat", changed)
    with pytest.raises(service.WorkspaceListingError) as caught:
        listing()
    assert caught.value.code == "directory_listing_changed"


@pytest.mark.parametrize("stage", ["open", "scan", "stat"])
@pytest.mark.parametrize("error,code", [
    (PermissionError("PRIVATE"), "directory_listing_access_denied"),
    (OSError(errno.EIO, "PRIVATE"), "directory_listing_unavailable"),
])
def test_io_failure_is_safe_and_resources_close(directory, resources, monkeypatch, stage, error, code):
    (directory / "item").touch()
    original_open = os.open

    def fail(*args, **kwargs):
        if stage == "open" and args[0] != directory.name:
            return original_open(*args, **kwargs)
        raise error

    if stage == "open":
        monkeypatch.setattr(os, "open", fail)
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {fail})
    elif stage == "scan":
        monkeypatch.setattr(os, "scandir", fail)
        monkeypatch.setattr(os, "supports_fd", os.supports_fd | {fail})
    else:
        monkeypatch.setattr(os.DirEntry, "stat", fail)
    with pytest.raises(service.WorkspaceListingError) as caught:
        listing()
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("kind,code", [("missing", "directory_listing_not_found"), ("file", "directory_listing_not_directory")])
def test_directory_missing_or_file(directory, resources, kind, code):
    directory.rmdir()
    if kind == "file":
        directory.touch()
    with pytest.raises(service.WorkspaceListingError) as caught:
        listing()
    assert caught.value.code == code


@pytest.mark.parametrize("capability", ["open", "scan", "flag"])
def test_missing_capability_refuses_fallback(directory, monkeypatch, capability):
    if capability == "open":
        monkeypatch.setattr(os, "supports_dir_fd", set())
    elif capability == "scan":
        monkeypatch.setattr(os, "supports_fd", set())
    else:
        monkeypatch.delattr(os, "O_NOFOLLOW")
    monkeypatch.setattr(service, "_list_resolved_directory", lambda *args: pytest.fail("no fallback"))
    with pytest.raises(service.WorkspaceListingError) as caught:
        listing()
    assert caught.value.code == "directory_listing_unsupported"


@pytest.mark.parametrize("path", [Path("relative"), Path("/a/../b")])
def test_internal_path_guard(path):
    with pytest.raises(service.WorkspaceListingError):
        service._list_resolved_directory(path)


def test_authorized_listing_closes_session_before_scan(database, target, root, monkeypatch):
    original = service._list_resolved_directory

    def scan(path):
        assert database[0] and all(session.closed for session in database[0])
        return original(path)

    monkeypatch.setattr(service, "_list_resolved_directory", scan)
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    result = service.list_task_directory(**args)
    assert result.entries == (service.WorkspaceDirectoryEntry("src", "directory"),)
    assert database[1] and all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])


@pytest.mark.parametrize("kind", ["foreign", "unbound"])
def test_authorization_and_binding_precede_enumeration(engine, database, target, monkeypatch, kind):
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    if kind == "foreign":
        args["user_id"] = target["other_id"]
    else:
        with Session(engine) as session, session.begin():
            session.scalar(select(Workspace)).root_path = None
    monkeypatch.setattr(service, "_list_resolved_directory", lambda *args: pytest.fail("must not enumerate"))
    error = WorkspaceNotAccessibleError if kind == "foreign" else WorkspacePathError
    with pytest.raises(error):
        service.list_task_directory(**args)
