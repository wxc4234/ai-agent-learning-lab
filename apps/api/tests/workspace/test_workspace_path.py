"""路径边界：真实文件系统、隔离 PostgreSQL 授权与只读事务。"""

from pathlib import Path, PurePosixPath

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace import workspace_path as service
from app.services.workspace.workspace_directory import WorkspaceDirectoryError
from tests.tasks import test_task_deletion_service as task_fixtures


# 复用已提交的任务/会话数据，仍由根夹具隔离并清理 PostgreSQL 资源。
target = task_fixtures.target


@pytest.fixture
def root(tmp_path):
    directory = tmp_path.resolve() / "project"
    directory.mkdir()
    (directory / "src").mkdir()
    (directory / "src" / "中文 file.txt").write_text("project content")
    return directory


@pytest.mark.parametrize("raw,expected", [
    (".", "."), ("./src//中文 file.txt", "src/中文 file.txt"),
    ("src/ file.txt", "src/ file.txt"), ("~/file", "~/file"),
    ("$HOME/file", "$HOME/file"), ("%2e%2e/file", "%2e%2e/file"),
])
def test_portable_paths_preserve_literal_names(raw, expected):
    assert service._parse_relative_path(raw) == PurePosixPath(expected)


@pytest.mark.parametrize("raw,code", [
    (None, "invalid_relative_path"), (42, "invalid_relative_path"),
    ("", "invalid_relative_path"), ("a\x00b", "invalid_relative_path"),
    ("/etc/passwd", "absolute_path_not_allowed"),
    ("C:/Windows/win.ini", "absolute_path_not_allowed"),
    ("C:notes.txt", "absolute_path_not_allowed"),
    (r"\Windows\file", "absolute_path_not_allowed"),
    (r"\\server\share\file", "absolute_path_not_allowed"),
    (r"\\?\C:\file", "absolute_path_not_allowed"),
    ("//server/share/file", "absolute_path_not_allowed"),
    (r"src\file", "invalid_relative_path"),
    ("../private", "parent_path_not_allowed"),
    ("src/../file", "parent_path_not_allowed"),
    ("file:stream", "invalid_relative_path"),
    ("NUL", "invalid_relative_path"), ("src/CON.txt", "invalid_relative_path"),
    ("COM1", "invalid_relative_path"), ("LPT1.txt", "invalid_relative_path"),
    ("file.", "invalid_relative_path"), ("file ", "invalid_relative_path"),
    ("a?b", "invalid_relative_path"), ("a*b", "invalid_relative_path"),
    ("a|b", "invalid_relative_path"), ("a<b", "invalid_relative_path"),
    ('a"b', "invalid_relative_path"), ("a\nb", "invalid_relative_path"),
])
def test_invalid_protocol_paths(raw, code):
    with pytest.raises(service.WorkspacePathError) as caught:
        service._parse_relative_path(raw)
    assert caught.value.code == code


@pytest.mark.parametrize("relative", [".", "src", "src/中文 file.txt"])
def test_real_existing_paths(root, relative):
    assert service._resolve_bound_path(str(root), PurePosixPath(relative)) == root / relative


@pytest.mark.parametrize("kind", ["file", "directory", "outside", "prefix-sibling", "broken", "cycle"])
def test_real_symbolic_links(root, kind):
    link = root / "link"
    expected = root / "src" / "中文 file.txt"
    code = None
    if kind == "file":
        link.symlink_to(expected)
        relative = "link"
    elif kind == "directory":
        link.symlink_to(root / "src", target_is_directory=True)
        relative = "link/中文 file.txt"
    elif kind in {"outside", "prefix-sibling"}:
        outside = root.parent / ("project-backup" if kind == "prefix-sibling" else "outside")
        outside.mkdir()
        (outside / "secret").write_text("must not read")
        link.symlink_to(outside, target_is_directory=True)
        relative = "link/secret"
        code = "path_outside_workspace"
    elif kind == "broken":
        link.symlink_to(root.parent / "missing")
        relative = "link"
        code = "workspace_path_not_found"
    else:
        link.symlink_to(root / "loop")
        (root / "loop").symlink_to(link)
        relative = "link"
        code = "workspace_path_unavailable"
    if code is None:
        assert service._resolve_bound_path(str(root), PurePosixPath(relative)) == expected
    else:
        with pytest.raises(service.WorkspacePathError) as caught:
            service._resolve_bound_path(str(root), PurePosixPath(relative))
        assert caught.value.code == code
        assert str(root.parent) not in str(caught.value)


@pytest.mark.parametrize("relative", ["missing", "src/中文 file.txt/child"])
def test_missing_or_non_directory_component(root, relative):
    with pytest.raises(service.WorkspacePathError) as caught:
        service._resolve_bound_path(str(root), PurePosixPath(relative))
    assert caught.value.code == "workspace_path_not_found"


def test_bound_root_retargeted_to_symbolic_link_is_rejected(root):
    moved = root.with_name("moved")
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(service.WorkspacePathError) as caught:
        service._resolve_bound_path(str(root), PurePosixPath("."))
    assert caught.value.code == "workspace_directory_changed"


@pytest.mark.parametrize("kind,code", [
    ("missing", "directory_not_found"), ("file", "not_a_directory"),
    ("relative", "invalid_directory_path"), ("root", "root_directory_not_allowed"),
])
def test_root_validation_errors_remain_classified(root, kind, code):
    raw = {"missing": str(root / "missing"), "file": str(root / "src" / "中文 file.txt"),
           "relative": "project", "root": root.anchor}[kind]
    with pytest.raises(WorkspaceDirectoryError) as caught:
        service._resolve_bound_path(raw, PurePosixPath("."))
    assert caught.value.code == code


@pytest.mark.parametrize("error,code", [
    (PermissionError("PRIVATE/path"), "workspace_path_access_denied"),
    (OSError("PRIVATE/path"), "workspace_path_unavailable"),
    (RuntimeError("PRIVATE/path"), "workspace_path_unavailable"),
    (ValueError("PRIVATE/path"), "workspace_path_unavailable"),
])
def test_target_errors_are_sanitized(root, monkeypatch, error, code):
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == root / "target":
            raise error
        return original(path, *args, **kwargs)

    # 权限异常用注入保证可重复；不把它称为真实 Windows/ACL 验证。
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(service.WorkspacePathError) as caught:
        service._resolve_bound_path(str(root), PurePosixPath("target"))
    assert caught.value.code == code
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.fixture
def database(engine, target, root, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local")
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = str(root)
    sessions = []
    statements = []

    class ReadSession(Session):
        closed = False

        def commit(self):
            pytest.fail("path service must not commit")

        def close(self):
            super().close()
            self.closed = True

    def factory():
        session = ReadSession(engine)
        sessions.append(session)
        return session

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(service, "SessionLocal", factory)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield sessions, statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        assert all(session.closed and not session.in_transaction() for session in sessions)


def read(target, **overrides):
    arguments = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    return service.resolve_task_workspace_path(**(arguments | {"relative_path": "."} | overrides))


def test_owned_task_reads_only_and_closes_before_filesystem(database, target, root, monkeypatch):
    original = service.validate_workspace_directory

    def validate(path):
        assert database[0] and all(session.closed for session in database[0])
        assert all(not session.in_transaction() for session in database[0])
        return original(path)

    monkeypatch.setattr(service, "validate_workspace_directory", validate)
    assert read(target, relative_path="src/中文 file.txt") == root / "src" / "中文 file.txt"
    assert database[1] and all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])


@pytest.mark.parametrize("kind", [
    "missing-workspace", "missing-task", "foreign-owner", "wrong-project",
    "foreign-conversation", "missing-conversation",
])
def test_authorization_precedes_path_parsing_and_filesystem(engine, database, target, monkeypatch, kind):
    overrides = {}
    with Session(engine) as session, session.begin():
        if kind == "missing-workspace":
            overrides["workspace_id"] = "f" * 32
        elif kind == "missing-task":
            overrides["task_id"] = "f" * 32
        elif kind == "foreign-owner":
            overrides["user_id"] = target["other_id"]
        elif kind == "wrong-project":
            session.add(Workspace(external_id="f" * 32, name="other", user_id=target["user_id"]))
            overrides["workspace_id"] = "f" * 32
        elif kind == "foreign-conversation":
            session.get(Conversation, target["conversation_pk"]).user_id = target["other_id"]
        else:
            session.delete(session.get(Conversation, target["conversation_pk"]))

    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized task must not reach path handling")

    monkeypatch.setattr(service, "_parse_relative_path", forbidden)
    monkeypatch.setattr(service, "_resolve_bound_path", forbidden)
    with pytest.raises(WorkspaceNotAccessibleError):
        read(target, **overrides)


def test_unbound_workspace_has_no_cwd_fallback(engine, database, target, monkeypatch):
    with Session(engine) as session, session.begin():
        session.scalar(select(Workspace)).root_path = None

    def forbidden(*args, **kwargs):
        pytest.fail("unbound project must not inspect filesystem")

    monkeypatch.setattr(service, "_resolve_bound_path", forbidden)
    with pytest.raises(service.WorkspacePathError) as caught:
        read(target)
    assert caught.value.code == "workspace_directory_unbound"


def test_database_failure_propagates_and_session_closes(database, target, monkeypatch):
    def fail(session, *args):
        session.execute(text("SELECT * FROM deliberately_missing_path_test_table"))

    monkeypatch.setattr(service, "owned_task", fail)
    with pytest.raises(DBAPIError):
        read(target)


def test_filesystem_failure_after_authorized_session_closes(database, target):
    with pytest.raises(service.WorkspacePathError) as caught:
        read(target, relative_path="missing")
    assert caught.value.code == "workspace_path_not_found"
    assert database[0][0].closed
