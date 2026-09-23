"""隔离 PostgreSQL 与自有样例目录验证清理待办的内部只读预检。"""

import os
from dataclasses import asdict
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Workspace, WorkspaceSampleOrigin
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.samples import cleanup_preflight as inspection
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError, TaskSampleBindings
from tests.workspace.samples.test_task_sample_binding import root, setup, target

__all__ = ["setup", "target"]


def _leave_real_pending(service, scope, engine, monkeypatch) -> Path:
    """只延迟本次物理清理；共用夹具退出时恢复并清理自有目录。"""

    service.bind(**scope)
    path = Path(root(engine))
    with monkeypatch.context() as patch:
        patch.setattr(service._registry, "close", lambda _handle: False)
        with pytest.raises(TaskSampleBindingError) as caught:
            service.close(**scope)
    assert caught.value.code == "sample_cleanup_incomplete"
    with Session(engine) as session:
        workspace = session.scalar(select(Workspace))
        origin = session.get(WorkspaceSampleOrigin, workspace.id)
        assert workspace.root_path is None
        assert origin.task_id is not None
        assert origin.lifecycle_state == "cleanup_pending"
        assert origin.root_path == str(path)
    return path


def _evidence(engine):
    with Session(engine) as session:
        workspace = session.scalar(select(Workspace))
        origin = session.get(WorkspaceSampleOrigin, workspace.id)
        return (
            workspace.root_path,
            None if origin is None else (
                origin.workspace_id, origin.task_id, origin.root_path, origin.lifecycle_state,
                origin.parent_dev, origin.parent_ino, origin.root_dev, origin.root_ino,
            ),
        )


def test_missing_evidence_returns_fixed_read_only_result(setup, engine, monkeypatch):
    service, scope, tracked, sessions = setup

    def unexpected(*_args, **_kwargs):
        pytest.fail("preflight must not create, borrow, close, or commit")

    with monkeypatch.context() as patch:
        patch.setattr(service._registry, "create", unexpected)
        patch.setattr(service._registry, "borrow", unexpected)
        patch.setattr(service._registry, "close", unexpected)
        patch.setattr(tracked, "commit", unexpected)
        result = service.read_cleanup_preflight(**scope)

    assert asdict(result) == {"result": "evidence_missing"}
    assert _evidence(engine) == (None, None)
    assert service._bindings == {}
    assert all(item.closed and not item.in_transaction() for item in sessions)


@pytest.mark.parametrize("scope_change", ["foreign_user", "unknown_task", "sibling_task"])
def test_preflight_requires_owned_source_task(setup, engine, target, monkeypatch, scope_change):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    before = _evidence(engine)
    other_scope = dict(scope)
    if scope_change == "foreign_user":
        other_scope["user_id"] = target["other_id"]
    elif scope_change == "unknown_task":
        other_scope["task_id"] = "f" * 32
    else:
        other_scope["task_id"] = "d" * 32

    with monkeypatch.context() as patch:
        patch.setattr(
            inspection, "inspect_pending_sample_directory",
            lambda _path: pytest.fail("unauthorized caller must not inspect the directory"),
        )
        with pytest.raises(WorkspaceNotAccessibleError) as caught:
            service.read_cleanup_preflight(**other_scope)

    assert str(path) not in str(caught.value)
    assert _evidence(engine) == before
    assert path.is_dir()


@pytest.mark.parametrize("change,expected", [
    ("none", "not_pending"),
    ("root_mismatch", "evidence_inconsistent"),
    ("pending_still_bound", "evidence_inconsistent"),
])
def test_active_or_inconsistent_origin_never_inspects_files(
    setup, engine, monkeypatch, change, expected,
):
    service, scope, _, _ = setup
    service.bind(**scope)
    path = Path(root(engine))
    if change != "none":
        with Session(engine) as session, session.begin():
            workspace = session.scalar(select(Workspace))
            origin = session.get(WorkspaceSampleOrigin, workspace.id)
            if change == "root_mismatch":
                workspace.root_path = "/different"
            else:
                origin.lifecycle_state = "cleanup_pending"
    before = _evidence(engine)
    with monkeypatch.context() as patch:
        patch.setattr(
            inspection, "inspect_pending_sample_directory",
            lambda _path: pytest.fail("only a consistent pending record may inspect files"),
        )
        result = service.read_cleanup_preflight(**scope)
    assert asdict(result) == {"result": expected}
    assert _evidence(engine) == before
    assert path.is_dir()


def test_real_pending_candidate_matches_record_and_has_no_side_effects(
    setup, engine, monkeypatch,
):
    service, scope, tracked, sessions = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    sample_file = path / "example.txt"
    before = _evidence(engine)
    root_before = path.stat(follow_symlinks=False)
    file_before = sample_file.stat(follow_symlinks=False)
    content_before = sample_file.read_bytes()
    bindings_before = dict(service._bindings)

    def unexpected(*_args, **_kwargs):
        pytest.fail("preflight must not create, borrow, close, or commit")

    with monkeypatch.context() as patch:
        patch.setattr(service._registry, "create", unexpected)
        patch.setattr(service._registry, "borrow", unexpected)
        patch.setattr(service._registry, "close", unexpected)
        patch.setattr(tracked, "commit", unexpected)
        result = service.read_cleanup_preflight(**scope)
        repeated = TaskSampleBindings().read_cleanup_preflight(**scope)

    assert asdict(result) == {"result": "identity_matches_record"}
    assert repeated == result
    assert str(path) not in repr(result)
    assert "root_path" not in repr(result)
    assert _evidence(engine) == before
    assert service._bindings == bindings_before
    assert sample_file.read_bytes() == content_before
    assert (path.stat(follow_symlinks=False).st_ino, path.stat(follow_symlinks=False).st_mtime_ns) == (
        root_before.st_ino, root_before.st_mtime_ns,
    )
    assert (
        sample_file.stat(follow_symlinks=False).st_ino,
        sample_file.stat(follow_symlinks=False).st_mtime_ns,
    ) == (file_before.st_ino, file_before.st_mtime_ns)
    assert all(item.closed and not item.in_transaction() for item in sessions)


def test_legacy_pending_candidate_remains_identity_unknown(setup, engine, monkeypatch):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    with Session(engine) as session, session.begin():
        origin = session.scalar(select(WorkspaceSampleOrigin))
        origin.parent_dev = None
        origin.parent_ino = None
        origin.root_dev = None
        origin.root_ino = None
    before = _evidence(engine)

    result = service.read_cleanup_preflight(**scope)

    assert asdict(result) == {"result": "identity_unverifiable"}
    assert _evidence(engine) == before
    assert path.is_dir()


@pytest.mark.parametrize("changed", ["parent", "root"])
def test_persisted_identity_mismatch_is_inconsistent_without_mutation(
    setup, engine, monkeypatch, changed,
):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    with Session(engine) as session, session.begin():
        origin = session.scalar(select(WorkspaceSampleOrigin))
        if changed == "parent":
            origin.parent_ino += 1
        else:
            origin.root_ino += 1
    before = _evidence(engine)
    content_before = (path / "example.txt").read_bytes()

    result = service.read_cleanup_preflight(**scope)

    assert asdict(result) == {"result": "evidence_inconsistent"}
    assert _evidence(engine) == before
    assert (path / "example.txt").read_bytes() == content_before


def test_pending_directory_missing_is_only_an_observation(setup, engine, monkeypatch):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    moved = path.with_name(path.name + ".held")
    path.rename(moved)
    try:
        before = _evidence(engine)
        assert asdict(service.read_cleanup_preflight(**scope)) == {"result": "directory_missing"}
        assert _evidence(engine) == before
    finally:
        moved.rename(path)
    assert path.is_dir()


@pytest.mark.parametrize("replacement", ["directory", "symlink", "file"])
def test_pending_path_replacement_never_proves_original_identity(
    setup, engine, monkeypatch, replacement,
):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    original = path.with_name(path.name + ".held")
    path.rename(original)
    try:
        if replacement == "directory":
            path.mkdir(mode=0o700)
        elif replacement == "symlink":
            path.symlink_to(original, target_is_directory=True)
        else:
            path.write_bytes(b"unrelated")
        assert asdict(service.read_cleanup_preflight(**scope)) == {
            "result": "evidence_inconsistent",
        }
        assert path.exists() or path.is_symlink()
    finally:
        if replacement == "directory":
            path.rmdir()
        else:
            path.unlink()
        original.rename(path)
    assert path.is_dir()


def test_pending_wrong_permissions_is_inconsistent(setup, engine, monkeypatch):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    path.chmod(0o755)
    try:
        assert asdict(service.read_cleanup_preflight(**scope)) == {
            "result": "evidence_inconsistent",
        }
    finally:
        path.chmod(0o700)


@pytest.mark.parametrize("raw_path", [
    "/etc/passwd",
    "/private/tmp/agent-proposal-../../other",
    "relative/invalid",
])
def test_pending_unsafe_persisted_path_never_opens_candidate(
    setup, engine, monkeypatch, raw_path,
):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    with Session(engine) as session, session.begin():
        session.scalar(select(WorkspaceSampleOrigin)).root_path = raw_path
    with monkeypatch.context() as patch:
        patch.setattr(
            inspection.os, "open",
            lambda *_args, **_kwargs: pytest.fail("unsafe persisted path must not be opened"),
        )
        result = service.read_cleanup_preflight(**scope)
    assert asdict(result) == {"result": "evidence_inconsistent"}
    assert path.is_dir()


def test_pending_valid_name_under_old_temp_parent_cannot_be_inspected(
    setup, engine, monkeypatch,
):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    old_parent_path = str(Path("/example-old-temporary-root") / path.name)
    with Session(engine) as session, session.begin():
        session.scalar(select(WorkspaceSampleOrigin)).root_path = old_parent_path
    with monkeypatch.context() as patch:
        patch.setattr(
            inspection.os, "open",
            lambda *_args, **_kwargs: pytest.fail("old parent must not be opened"),
        )
        result = service.read_cleanup_preflight(**scope)
    assert asdict(result) == {"result": "inspection_unavailable"}
    assert path.is_dir()


def test_pending_filesystem_error_is_unavailable_without_raw_error(
    setup, engine, monkeypatch,
):
    service, scope, _, _ = setup
    path = _leave_real_pending(service, scope, engine, monkeypatch)
    real_stat = inspection.os.stat

    def fail_candidate(candidate, *args, **kwargs):
        if candidate == path.name and kwargs.get("dir_fd") is not None:
            raise OSError("private path information")
        return real_stat(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(inspection.os, "stat", fail_candidate)
        result = service.read_cleanup_preflight(**scope)
    assert asdict(result) == {"result": "inspection_unavailable"}
    assert "private path information" not in repr(result)
    assert path.is_dir()


def test_parent_fd_close_error_does_not_escape_or_claim_identity(tmp_path, monkeypatch):
    path = tmp_path / ("agent-proposal-" + "a" * 32)
    path.mkdir(mode=0o700)
    original_close = inspection.os.close

    def fail_after_close(fd):
        original_close(fd)
        raise OSError("private descriptor error")

    with monkeypatch.context() as patch:
        patch.setattr(inspection.tempfile, "gettempdir", lambda: str(tmp_path))
        patch.setattr(inspection.os, "close", fail_after_close)
        result = inspection.inspect_pending_sample_directory(str(path))
    assert result == "inspection_unavailable"
    assert path.is_dir()


def test_partial_expected_identity_is_rejected_before_file_access(tmp_path, monkeypatch):
    path = tmp_path / ("agent-proposal-" + "a" * 32)

    def unexpected(*_args, **_kwargs):
        pytest.fail("partial expected identity must not access files")

    with monkeypatch.context() as patch:
        patch.setattr(inspection.os, "open", unexpected)
        result = inspection.inspect_pending_sample_directory(
            str(path),
            expected_parent_identity=(1, 2),
        )
    assert result == "evidence_inconsistent"


@pytest.mark.parametrize("has_recorded_identity", [False, True])
def test_candidate_is_opened_without_following_links_and_all_fds_are_closed(
    tmp_path, monkeypatch, has_recorded_identity,
):
    path = tmp_path / ("agent-proposal-" + "a" * 32)
    path.mkdir(mode=0o700)
    parent_info = tmp_path.stat(follow_symlinks=False)
    candidate_info = path.stat(follow_symlinks=False)
    opened = []
    candidate_opens = []
    native_open = os.open

    def capture_open(candidate, flags, *args, **kwargs):
        descriptor = native_open(candidate, flags, *args, **kwargs)
        opened.append(descriptor)
        if candidate == path.name:
            candidate_opens.append((flags, kwargs.get("dir_fd"), descriptor))
        return descriptor

    with monkeypatch.context() as patch:
        patch.setattr(inspection.tempfile, "gettempdir", lambda: str(tmp_path))
        patch.setattr(inspection.os, "open", capture_open)
        result = inspection.inspect_pending_sample_directory(
            str(path),
            expected_parent_identity=(parent_info.st_dev, parent_info.st_ino)
            if has_recorded_identity else None,
            expected_root_identity=(candidate_info.st_dev, candidate_info.st_ino)
            if has_recorded_identity else None,
        )

    assert result == (
        "identity_matches_record" if has_recorded_identity else "identity_unverifiable"
    )
    assert len(candidate_opens) == 1
    flags, parent_fd, candidate_fd = candidate_opens[0]
    assert flags & os.O_DIRECTORY
    assert flags & os.O_NOFOLLOW
    assert parent_fd in opened and parent_fd != candidate_fd
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_candidate_entry_changed_between_stat_and_open_is_unavailable(tmp_path, monkeypatch):
    path = tmp_path / ("agent-proposal-" + "a" * 32)
    path.mkdir(mode=0o700)
    held = path.with_name(path.name + ".held")
    parent_info = tmp_path.stat(follow_symlinks=False)
    candidate_info = path.stat(follow_symlinks=False)
    native_open = os.open
    swapped = False

    def replace_before_open(candidate, flags, *args, **kwargs):
        nonlocal swapped
        if candidate == path.name and not swapped:
            path.rename(held)
            path.mkdir(mode=0o700)
            swapped = True
        return native_open(candidate, flags, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(inspection.tempfile, "gettempdir", lambda: str(tmp_path))
            patch.setattr(inspection.os, "open", replace_before_open)
            result = inspection.inspect_pending_sample_directory(
                str(path),
                expected_parent_identity=(parent_info.st_dev, parent_info.st_ino),
                expected_root_identity=(candidate_info.st_dev, candidate_info.st_ino),
            )
        assert swapped
        assert result == "inspection_unavailable"
    finally:
        if swapped:
            path.rmdir()
            held.rename(path)
    assert path.is_dir()


@pytest.mark.parametrize("has_recorded_identity", [False, True])
@pytest.mark.parametrize("replacement", [False, True])
def test_candidate_entry_changed_or_removed_after_open_is_unavailable(
    tmp_path, monkeypatch, has_recorded_identity, replacement,
):
    path = tmp_path / ("agent-proposal-" + "a" * 32)
    path.mkdir(mode=0o700)
    held = path.with_name(path.name + ".held")
    parent_info = tmp_path.stat(follow_symlinks=False)
    candidate_info = path.stat(follow_symlinks=False)
    native_open = os.open
    swapped = False

    def replace_after_open(candidate, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = native_open(candidate, flags, *args, **kwargs)
        if candidate == path.name and not swapped:
            path.rename(held)
            if replacement:
                path.mkdir(mode=0o700)
            swapped = True
        return descriptor

    try:
        with monkeypatch.context() as patch:
            patch.setattr(inspection.tempfile, "gettempdir", lambda: str(tmp_path))
            patch.setattr(inspection.os, "open", replace_after_open)
            result = inspection.inspect_pending_sample_directory(
                str(path),
                expected_parent_identity=(parent_info.st_dev, parent_info.st_ino)
                if has_recorded_identity else None,
                expected_root_identity=(candidate_info.st_dev, candidate_info.st_ino)
                if has_recorded_identity else None,
            )
        assert swapped
        assert result == "inspection_unavailable"
    finally:
        if swapped:
            if replacement:
                path.rmdir()
            held.rename(path)
    assert path.is_dir()


def test_candidate_open_failure_is_unavailable_without_raw_error(tmp_path, monkeypatch):
    path = tmp_path / ("agent-proposal-" + "a" * 32)
    path.mkdir(mode=0o700)
    native_open = os.open

    def fail_candidate_open(candidate, flags, *args, **kwargs):
        if candidate == path.name:
            raise OSError("private candidate path")
        return native_open(candidate, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(inspection.tempfile, "gettempdir", lambda: str(tmp_path))
        patch.setattr(inspection.os, "open", fail_candidate_open)
        result = inspection.inspect_pending_sample_directory(str(path))

    assert result == "inspection_unavailable"
    assert "private candidate path" not in result
    assert path.is_dir()


def test_candidate_fd_stat_failure_is_unavailable_and_all_fds_close(
    tmp_path, monkeypatch,
):
    path = tmp_path / ("agent-proposal-" + "a" * 32)
    path.mkdir(mode=0o700)
    opened = []
    candidate_fd = None
    native_open = os.open
    native_fstat = os.fstat

    def capture_open(candidate, flags, *args, **kwargs):
        nonlocal candidate_fd
        descriptor = native_open(candidate, flags, *args, **kwargs)
        opened.append(descriptor)
        if candidate == path.name:
            candidate_fd = descriptor
        return descriptor

    def fail_candidate_stat(descriptor):
        if descriptor == candidate_fd:
            raise OSError("private candidate descriptor")
        return native_fstat(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(inspection.tempfile, "gettempdir", lambda: str(tmp_path))
        patch.setattr(inspection.os, "open", capture_open)
        patch.setattr(inspection.os, "fstat", fail_candidate_stat)
        result = inspection.inspect_pending_sample_directory(str(path))

    assert candidate_fd is not None
    assert result == "inspection_unavailable"
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert path.is_dir()


def test_candidate_fd_close_failure_is_unavailable_and_parent_fd_closes(
    tmp_path, monkeypatch,
):
    path = tmp_path / ("agent-proposal-" + "a" * 32)
    path.mkdir(mode=0o700)
    opened = []
    candidate_fd = None
    native_open = os.open
    native_close = os.close

    def capture_open(candidate, flags, *args, **kwargs):
        nonlocal candidate_fd
        descriptor = native_open(candidate, flags, *args, **kwargs)
        opened.append(descriptor)
        if candidate == path.name:
            candidate_fd = descriptor
        return descriptor

    def fail_after_candidate_close(descriptor):
        native_close(descriptor)
        if descriptor == candidate_fd:
            raise OSError("private candidate descriptor")

    with monkeypatch.context() as patch:
        patch.setattr(inspection.tempfile, "gettempdir", lambda: str(tmp_path))
        patch.setattr(inspection.os, "open", capture_open)
        patch.setattr(inspection.os, "close", fail_after_candidate_close)
        result = inspection.inspect_pending_sample_directory(str(path))

    assert candidate_fd is not None
    assert result == "inspection_unavailable"
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert path.is_dir()
