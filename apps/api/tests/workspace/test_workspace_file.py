"""受限文本读取：真实描述符、确定性路径替换与直接授权集成。"""

import errno
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.services.workspace import workspace_file as service
from tests.workspace import test_workspace_path as path_tests


# 仅复用上一课的隔离 PostgreSQL 夹具，不扩展到整个领域回归。
root = path_tests.root
target = path_tests.target
database = path_tests.database


@pytest.fixture
def file(tmp_path, monkeypatch):
    directory = tmp_path.resolve() / "project"
    directory.mkdir()
    path = directory / "sample.txt"
    path.write_bytes(b"hello")
    monkeypatch.setattr(service, "resolve_task_workspace_path", lambda **kwargs: path)
    return path


def read():
    return service.read_task_text_file(
        user_id=1, workspace_id="workspace", task_id="task",
        relative_path="./sample.txt",
    )


@pytest.fixture
def descriptors(monkeypatch):
    original_open = os.open
    original_close = os.close
    active = set()
    opened = []

    def tracked_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        active.add(descriptor)
        opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor):
        original_close(descriptor)
        active.remove(descriptor)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", tracked_close)
    # 包装函数仍委托真实 os.open；保留能力探测语义。
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {tracked_open})
    yield opened, active
    assert not active, "service leaked file descriptors"


@pytest.mark.parametrize("data", [b"", b"hello\r\n", "你好\n".encode(), b"a" * (256 * 1024)])
def test_text_and_exact_limit(file, descriptors, data):
    file.write_bytes(data)
    result = read()
    assert result.content == data.decode("utf-8")
    assert result.byte_count == len(data)
    assert result.relative_path == "sample.txt"
    assert descriptors[0] and not descriptors[1]
    with pytest.raises(FrozenInstanceError):
        result.content = "changed"


@pytest.mark.parametrize("data,code", [
    (b"a" * (256 * 1024 + 1), "file_too_large"),
    (b"\xff", "file_not_utf8_text"), (b"hello\x00", "file_not_utf8_text"),
])
def test_rejected_content(file, descriptors, data, code):
    file.write_bytes(data)
    with pytest.raises(service.WorkspaceFileError) as caught:
        read()
    assert caught.value.code == code


@pytest.mark.parametrize("kind", ["directory", "fifo", "symlink"])
def test_nonregular_target_never_read(file, descriptors, monkeypatch, kind):
    file.unlink()
    if kind == "directory":
        file.mkdir()
    elif kind == "fifo":
        os.mkfifo(file)
    else:
        outside = file.parent.parent / "private"
        outside.write_bytes(b"secret")
        file.symlink_to(outside)
    monkeypatch.setattr(os, "read", lambda *args: pytest.fail("must not read special target"))
    with pytest.raises(service.WorkspaceFileError) as caught:
        read()
    assert caught.value.code == "file_not_regular"


@pytest.mark.parametrize("kind", ["leaf-link", "parent-link", "regular", "fifo"])
def test_replacement_during_open(file, descriptors, monkeypatch, kind):
    original_open = os.open
    outside = file.parent.parent / "outside"
    outside.mkdir()
    (outside / file.name).write_bytes(b"secret")
    replaced = False

    def replace_then_open(name, flags, *args, **kwargs):
        nonlocal replaced
        trigger = file.parent.name if kind == "parent-link" else file.name
        if name == trigger and not replaced:
            replaced = True
            if kind == "parent-link":
                file.parent.rename(file.parent.with_name("old-project"))
                file.parent.symlink_to(outside, target_is_directory=True)
            else:
                file.rename(file.with_name("original"))
                if kind == "leaf-link":
                    file.symlink_to(outside / file.name)
                elif kind == "fifo":
                    assert flags & os.O_NONBLOCK
                    os.mkfifo(file)
                else:
                    file.write_bytes(b"replacement")
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_then_open)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {replace_then_open})
    monkeypatch.setattr(os, "read", lambda *args: pytest.fail("replacement must not be read"))
    with pytest.raises(service.WorkspaceFileError) as caught:
        read()
    assert replaced
    assert caught.value.code == ("file_not_regular" if kind == "fifo" else "file_changed")


@pytest.mark.parametrize("kind", ["grow", "shrink", "same-size", "oversize"])
def test_change_during_read(file, descriptors, monkeypatch, kind):
    original_read = os.read
    changed = False
    total = 0

    def change_then_read(descriptor, count):
        nonlocal changed, total
        if not changed:
            changed = True
            data = {"grow": b"longer", "shrink": b"a", "same-size": b"other",
                    "oversize": b"a" * (service.MAX_TEXT_FILE_BYTES + 20)}[kind]
            file.write_bytes(data)
            # 显式改变时间，避免快速同长度写入依赖文件系统时间精度。
            os.utime(file, ns=(1_000_000_000, 2_000_000_000))
        chunk = original_read(descriptor, count)
        total += len(chunk)
        return chunk

    monkeypatch.setattr(os, "read", change_then_read)
    with pytest.raises(service.WorkspaceFileError) as caught:
        read()
    assert caught.value.code == ("file_too_large" if kind == "oversize" else "file_changed")
    assert total <= service.MAX_TEXT_FILE_BYTES + 1


@pytest.mark.parametrize("error,code", [
    (FileNotFoundError("PRIVATE"), "file_not_found"),
    (PermissionError("PRIVATE"), "file_access_denied"),
    (OSError(errno.EIO, "PRIVATE"), "file_unavailable"),
])
@pytest.mark.parametrize("stage", ["open", "fstat", "read"])
def test_io_failures_are_safe_and_close_descriptors(file, descriptors, monkeypatch, error, code, stage):
    original_open = os.open

    def failed(*args, **kwargs):
        if stage == "open" and args[0] != file.name:
            return original_open(*args, **kwargs)
        raise error

    monkeypatch.setattr(os, stage, failed)
    if stage == "open":
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {failed})
    with pytest.raises(service.WorkspaceFileError) as caught:
        read()
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("capability", ["dir_fd", "stat_no_follow", "flag"])
def test_missing_capability_has_no_fallback(file, monkeypatch, capability):
    if capability == "dir_fd":
        monkeypatch.setattr(os, "supports_dir_fd", set())
    elif capability == "stat_no_follow":
        monkeypatch.setattr(os, "supports_follow_symlinks", set())
    else:
        monkeypatch.delattr(os, "O_NOFOLLOW")
    monkeypatch.setattr(service, "_read_resolved_file", lambda *args: pytest.fail("no fallback"))
    with pytest.raises(service.WorkspaceFileError) as caught:
        read()
    assert caught.value.code == "file_read_unsupported"


def test_short_reads_are_accumulated(file, descriptors, monkeypatch):
    original_read = os.read
    monkeypatch.setattr(os, "read", lambda fd, count: original_read(fd, min(count, 2)))
    assert read().content == "hello"


@pytest.mark.parametrize("path", [Path("relative"), Path("/a/../b")])
def test_internal_reader_rejects_noncanonical_input(path):
    with pytest.raises(service.WorkspaceFileError) as caught:
        service._read_resolved_file(path)
    assert caught.value.code == "file_unavailable"


def test_real_authorization_to_read(database, target, root, monkeypatch):
    original_read = os.read

    def checked_read(fd, count):
        assert database[0] and all(session.closed for session in database[0])
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", checked_read)
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    (root / "alias").symlink_to(root / "src" / "中文 file.txt")
    result = service.read_task_text_file(**args, relative_path="alias")
    assert result.content == "project content"
    assert result.relative_path == "alias"
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])


def test_real_authorization_rejection_precedes_read(database, target, monkeypatch):
    from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError

    monkeypatch.setattr(service, "_read_resolved_file", lambda *args: pytest.fail("unauthorized read"))
    with pytest.raises(WorkspaceNotAccessibleError):
        service.read_task_text_file(
            user_id=target["other_id"], workspace_id=target["workspace_id"],
            task_id=target["task_id"], relative_path=".",
        )
