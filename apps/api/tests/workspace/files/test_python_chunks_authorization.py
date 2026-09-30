"""服务边界：真实本地归属、隔离PostgreSQL、受限文件读取和事务关闭。"""

from dataclasses import asdict
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace.directory import workspace_path
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.files import (
    python_chunks as service,
    python_symbols,
    workspace_file,
    workspace_listing,
)
from app.services.workspace.files.code_inventory import CodeInventoryError
from app.services.workspace.files.workspace_file import WorkspaceFileError
from tests.assertions import require_value
from tests.tasks.test_task_deletion_service import target
from tests.workspace.directory.test_workspace_path import database


__all__ = ["database", "target"]


@pytest.fixture
def root(tmp_path):
    project = tmp_path.resolve() / "code"
    project.mkdir()
    (project / ".gitignore").write_text("ignored.py\n")
    (project / "safe.py").write_bytes(
        b"class Outer:\r\n    def run(self):\r\n        return 'SOURCE_PRIVATE'\r\n"
    )
    (project / "ignored.py").write_text("INVALID PRIVATE")
    (project / ".env").write_text("PRIVATE")
    return project


def build(target, **overrides):
    arguments = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    arguments.update(overrides)
    return service.build_python_code_chunks(**arguments)


def snapshot(root):
    return {
        str(file.relative_to(root)): (
            file.read_bytes(),
            file.stat().st_ino,
            file.stat().st_mtime_ns,
        )
        for file in root.rglob("*")
        if file.is_file() and not file.is_symlink()
    }


def test_authorized_local_source_is_read_only_and_all_sessions_close_before_io_and_chunking(
    database, target, root, monkeypatch
):
    assert settings.app_mode == "local"
    before = snapshot(root)
    original_read = workspace_file._read_resolved_file
    original_list = workspace_listing._list_resolved_directory
    original_chunk = service._chunk
    observed = []

    def closed():
        assert database[0] and all(
            session.closed and not session.in_transaction() for session in database[0]
        )

    def reading(path):
        closed()
        observed.append("read")
        return original_read(path)

    def listing(path):
        closed()
        observed.append("list")
        return original_list(path)

    def chunk(*args):
        closed()
        observed.append("chunk")
        return original_chunk(*args)

    monkeypatch.setattr(workspace_file, "_read_resolved_file", reading)
    monkeypatch.setattr(workspace_listing, "_list_resolved_directory", listing)
    monkeypatch.setattr(service, "_chunk", chunk)
    result = build(target)
    assert [chunk.symbol.qualified_name for chunk in result.chunks] == [
        "Outer",
        "Outer.run",
    ]
    assert result.chunks[0].text == "class Outer:\n"
    assert (
        result.chunks[1].text == "    def run(self):\n        return 'SOURCE_PRIVATE'\n"
    )
    assert (
        result.chunks[1].symbol.sha256
        == sha256((root / "safe.py").read_bytes()).hexdigest()
    )
    assert all(kind in observed for kind in ("read", "list", "chunk"))
    assert database[1] and all(
        sql.lstrip().upper().startswith("SELECT") for sql in database[1]
    )
    assert snapshot(root) == before and str(root) not in repr(asdict(result))


@pytest.mark.parametrize(
    "kind",
    [
        "missing-task",
        "wrong-project",
        "foreign-owner",
        "foreign-conversation",
        "missing-conversation",
    ],
)
def test_no_authorized_scope_means_no_filesystem_or_parse(
    database, target, engine, monkeypatch, kind
):
    overrides = {}
    with Session(engine) as session, session.begin():
        if kind == "missing-task":
            overrides["task_id"] = "f" * 32
        elif kind == "wrong-project":
            session.add(
                Workspace(external_id="f" * 32, name="other", user_id=target["user_id"])
            )
            overrides["workspace_id"] = "f" * 32
        elif kind == "foreign-owner":
            require_value(session.scalar(select(Workspace))).user_id = target[
                "other_id"
            ]
        elif kind == "foreign-conversation":
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
        else:
            session.delete(session.get(Conversation, target["conversation_pk"]))
    monkeypatch.setattr(
        workspace_path,
        "_resolve_bound_path",
        lambda **kwargs: pytest.fail("unauthorized filesystem"),
    )
    monkeypatch.setattr(
        service,
        "_parse_python_symbols",
        lambda *args, **kwargs: pytest.fail("unauthorized parse"),
    )
    with pytest.raises(WorkspaceNotAccessibleError):
        build(target, **overrides)


def test_unbound_scope_never_uses_process_cwd(database, target, engine, monkeypatch):
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(Workspace))).root_path = None
    monkeypatch.setattr(
        workspace_path,
        "_resolve_bound_path",
        lambda **kwargs: pytest.fail("unbound filesystem"),
    )
    with pytest.raises(WorkspacePathError) as caught:
        build(target)
    assert caught.value.code == "workspace_directory_unbound"


@pytest.mark.parametrize("change", ["binding", "workspace-owner", "conversation-owner"])
def test_change_after_collected_chunk_discards_entire_result(
    database, target, root, engine, monkeypatch, change
):
    original = service._chunk
    changed = False
    monkeypatch.setattr(service, "MAX_CODE_CHUNKS", 1)

    def chunk(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            changed = True
            with Session(engine) as session, session.begin():
                if change == "binding":
                    replacement = root.parent / "replacement"
                    replacement.mkdir()
                    require_value(session.scalar(select(Workspace))).root_path = str(
                        replacement
                    )
                elif change == "workspace-owner":
                    require_value(session.scalar(select(Workspace))).user_id = target[
                        "other_id"
                    ]
                else:
                    require_value(
                        session.get(Conversation, target["conversation_pk"])
                    ).user_id = target["other_id"]
        return result

    monkeypatch.setattr(service, "_chunk", chunk)
    expected = (
        WorkspacePathError if change == "binding" else WorkspaceNotAccessibleError
    )
    with pytest.raises(expected) as caught:
        build(target)
    assert (
        changed
        and "SOURCE_PRIVATE" not in str(caught.value)
        and str(root) not in str(caught.value)
    )
    if change == "binding":
        assert caught.value.code == "workspace_directory_changed"


def test_policy_change_after_collected_chunk_discards_entire_result(
    database, target, root, monkeypatch
):
    original = service._chunk

    def chunk(*args):
        result = original(*args)
        (root / ".gitignore").write_text("safe.py\n")
        return result

    monkeypatch.setattr(service, "_chunk", chunk)
    with pytest.raises(CodeInventoryError) as caught:
        build(target)
    assert caught.value.code == "code_ignore_changed" and "SOURCE_PRIVATE" not in str(
        caught.value
    )


def test_link_replacement_after_resolve_is_rejected_before_parser(
    database, target, root, monkeypatch
):
    original = workspace_path._resolve_bound_path

    def resolve(**kwargs):
        path = original(**kwargs)
        if path == root / "safe.py":
            path.unlink()
            path.symlink_to(root / ".env")
        return path

    monkeypatch.setattr(workspace_path, "_resolve_bound_path", resolve)
    monkeypatch.setattr(
        service,
        "_parse_python_symbols",
        lambda *args, **kwargs: pytest.fail("linked parse"),
    )
    with pytest.raises(WorkspaceFileError) as caught:
        build(target)
    assert caught.value.code == "file_not_regular"


@pytest.mark.parametrize(
    "failure", ["syntax", "parse-budget", "resource", "output-budget"]
)
def test_parser_and_serialization_failures_are_fixed_not_partial(
    database, target, root, monkeypatch, failure
):
    if failure == "syntax":
        (root / "safe.py").write_text("def SOURCE_PRIVATE(:\n")
    elif failure == "parse-budget":
        monkeypatch.setattr(python_symbols, "MAX_PYTHON_BYTES", 1)
    elif failure == "output-budget":
        monkeypatch.setattr(service, "MAX_CHUNK_RESULT_BYTES", 1)
    else:

        def fail(*args, **kwargs):
            raise RecursionError("SOURCE_PRIVATE /host/path")

        monkeypatch.setattr(python_symbols.ast, "parse", fail)
    with pytest.raises(python_symbols.PythonSymbolsError) as caught:
        build(target)
    assert (
        caught.value.code
        == {
            "syntax": "python_syntax_invalid",
            "parse-budget": "python_parse_budget_exceeded",
            "resource": "python_parse_budget_exceeded",
            "output-budget": "python_chunks_result_too_large",
        }[failure]
    )
    assert "SOURCE_PRIVATE" not in str(caught.value) and "/host/path" not in str(
        caught.value
    )


def test_output_budget_does_not_hide_later_source_failure(
    database, target, root, monkeypatch
):
    monkeypatch.setattr(service, "MAX_CODE_CHUNKS", 1)
    (root / "z.py").write_text("def SOURCE_PRIVATE(:\n")
    with pytest.raises(python_symbols.PythonSymbolsError) as caught:
        build(target)
    assert caught.value.code == "python_syntax_invalid" and "SOURCE_PRIVATE" not in str(
        caught.value
    )
