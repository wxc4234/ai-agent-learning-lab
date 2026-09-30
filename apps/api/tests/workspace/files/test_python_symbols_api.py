"""已注册符号API：真实本机身份/隔离PostgreSQL/文件I/O与安全失败。"""

from hashlib import sha256

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import Conversation, Workspace
from app.routers.workspace import code_inventory as route
from app.services.workspace.directory import workspace_path
from app.services.workspace.files import code_inventory, python_symbols as service
from app.services.workspace.files.workspace_file import WorkspaceFileError
from tests.assertions import require_value
from tests.workspace.files.test_code_inventory_api import (
    HEADERS,
    TOKEN,
    api,
    database,
    snapshot,
    target,
)


__all__ = ["api", "database", "target"]


@pytest.fixture
def root(tmp_path):
    project = tmp_path.resolve() / "python-code"
    project.mkdir()
    (project / ".gitignore").write_text("ignored.py\n")
    (project / "safe.py").write_bytes(
        b"# SOURCE_PRIVATE\r\ndef safe():\r\n    return 1\r\n"
    )
    (project / "ignored.py").write_text("INVALID_PRIVATE[\n")
    (project / ".env").write_text("SOURCE_PRIVATE")
    return project


@pytest.fixture
def symbol_api(api):
    return api[0], api[1].replace("/code-inventory", "/python-symbols")


def get(api, **kwargs):
    return api[0].get(api[1], headers=kwargs.pop("headers", HEADERS), **kwargs)


def test_registered_symbols_exact_source_no_execution_writes_or_open_session(
    symbol_api, root, database, engine, monkeypatch
):
    marker = root / "must-not-exist"
    file = root / "safe.py"
    file.write_text(
        f"open({str(marker)!r}, 'w').write('SOURCE_PRIVATE')\n"
        "@never_execute()\n"
        "async def safe():\n"
        "    return missing_call()\n"
    )
    before = snapshot(root)
    data = file.read_bytes()
    digest = sha256(data).hexdigest()
    statements = []
    parsed = []
    original = service.parse_python_symbols

    def parse(file, content):
        assert database[0] and all(session.closed for session in database[0])
        parsed.append(file.relative_path)
        return original(file, content)

    def capture(connection, cursor, sql, parameters, context, executemany):
        statements.append(sql.lower().lstrip())

    monkeypatch.setattr(service, "parse_python_symbols", parse)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = get(symbol_api)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    body = response.json()
    assert body["source"] == "authorized_python_symbols"
    assert (
        body["parser"] == "python_ast_3_12" and body["policy"] == "gitignore_subset_v1"
    )
    assert body["workspace_id"] == "a" * 32 and body["task_id"] == "b" * 32
    assert body["files"] == [
        {
            "relative_path": "safe.py",
            "file_type": "source",
            "language": "python",
            "byte_count": len(data),
            "sha256": digest,
        }
    ]
    assert body["symbols"] == [
        {
            "relative_path": "safe.py",
            "name": "safe",
            "qualified_name": "safe",
            "kind": "async_function",
            "start_line": 2,
            "definition_line": 3,
            "end_line": 4,
            "sha256": digest,
        }
    ]
    assert (
        body["parsed_files"]
        == body["inspected_files"]
        == body["scanned_directories"]
        == 1
    )
    assert not body["truncated"] and body["incomplete_reasons"] == []
    assert parsed == ["safe.py"] and snapshot(root) == before and not marker.exists()
    assert all(
        value not in response.text for value in [str(root), "SOURCE_PRIVATE", TOKEN]
    )
    assert statements and all(
        sql.startswith("select")
        or ("insert into users" in sql and "on conflict" in sql)
        for sql in statements
    )


@pytest.mark.parametrize(
    "kind", ["missing-task", "wrong-project", "workspace-owner", "conversation-owner"]
)
def test_ownership_precedes_filesystem_and_parser(
    symbol_api, engine, target, monkeypatch, kind
):
    path = symbol_api[1]
    with Session(engine) as session, session.begin():
        if kind == "missing-task":
            path = path.replace(target["task_id"], "f" * 32)
        elif kind == "wrong-project":
            session.add(
                Workspace(external_id="f" * 32, name="other", user_id=target["user_id"])
            )
            path = path.replace(target["workspace_id"], "f" * 32)
        elif kind == "workspace-owner":
            require_value(session.scalar(select(Workspace))).user_id = target[
                "other_id"
            ]
        else:
            require_value(
                session.get(Conversation, target["conversation_pk"])
            ).user_id = target["other_id"]
    monkeypatch.setattr(
        workspace_path,
        "_resolve_bound_path",
        lambda **kwargs: pytest.fail("unauthorized FS"),
    )
    monkeypatch.setattr(
        service, "parse_python_symbols", lambda *args: pytest.fail("unauthorized parse")
    )
    response = symbol_api[0].get(path, headers=HEADERS)
    assert (
        response.status_code == 404
        and response.json()["code"] == "workspace_not_accessible"
    )


def test_unbound_scope_is_not_cwd_scan(symbol_api, engine, monkeypatch):
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(Workspace))).root_path = None
    monkeypatch.setattr(
        workspace_path,
        "_resolve_bound_path",
        lambda **kwargs: pytest.fail("unbound FS"),
    )
    response = get(symbol_api)
    assert (
        response.status_code == 422
        and response.json()["code"] == "workspace_directory_unbound"
    )


@pytest.mark.parametrize(
    "change", ["binding", "workspace-owner", "conversation-owner", "rule"]
)
def test_change_after_parsing_discards_collected_symbols(
    symbol_api, root, engine, target, monkeypatch, change
):
    original = service.parse_python_symbols

    def parse(file, content):
        result = original(file, content)
        if change == "rule":
            (root / ".gitignore").write_text("safe.py\n")
        else:
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

    monkeypatch.setattr(service, "parse_python_symbols", parse)
    response = get(symbol_api)
    assert response.status_code == (409 if change in {"binding", "rule"} else 404)
    assert response.json()["code"] == (
        "workspace_directory_changed"
        if change == "binding"
        else "code_ignore_changed"
        if change == "rule"
        else "workspace_not_accessible"
    )
    assert "symbols" not in response.json() and "files" not in response.json()
    assert "SOURCE_PRIVATE" not in response.text


@pytest.mark.parametrize("stage", ["link-existing", "link-after-resolve"])
def test_links_never_supply_python_symbols(symbol_api, root, monkeypatch, stage):
    if stage == "link-existing":
        (root / "link.py").symlink_to(root / "safe.py")
        response = get(symbol_api)
        assert response.status_code == 200 and [
            item["relative_path"] for item in response.json()["files"]
        ] == ["safe.py"]
        assert response.json()["excluded_counts"]["link_or_special"] == 1
    else:
        original = workspace_path._resolve_bound_path

        def resolve(**kwargs):
            path = original(**kwargs)
            if path == root / "safe.py":
                path.unlink()
                path.symlink_to(root / ".env")
            return path

        monkeypatch.setattr(workspace_path, "_resolve_bound_path", resolve)
        monkeypatch.setattr(
            service, "parse_python_symbols", lambda *args: pytest.fail("linked parse")
        )
        response = get(symbol_api)
        assert (
            response.status_code == 422
            and response.json()["code"] == "file_not_regular"
        )


@pytest.mark.parametrize(
    "failure", ["syntax", "ignore", "parse-budget", "output-budget", "resource"]
)
def test_known_failures_return_fixed_error_not_empty_or_partial(
    symbol_api, root, monkeypatch, failure
):
    if failure == "syntax":
        (root / "safe.py").write_text("def SOURCE_PRIVATE(:\n")
    elif failure == "ignore":
        (root / ".gitignore").write_text("[SOURCE_PRIVATE\n")
    elif failure == "parse-budget":
        monkeypatch.setattr(service, "MAX_PYTHON_BYTES", 1)
    elif failure == "output-budget":
        monkeypatch.setattr(service, "MAX_SYMBOL_RESULT_BYTES", 1)
    else:

        def fail(*args, **kwargs):
            raise RecursionError("SOURCE_PRIVATE /host/path")

        monkeypatch.setattr(service.ast, "parse", fail)
    response = get(symbol_api)
    assert (
        response.status_code == 422 and response.headers["cache-control"] == "no-store"
    )
    assert (
        response.json()["code"]
        == {
            "syntax": "python_syntax_invalid",
            "ignore": "unsupported_gitignore",
            "parse-budget": "python_parse_budget_exceeded",
            "output-budget": "python_symbols_result_too_large",
            "resource": "python_parse_budget_exceeded",
        }[failure]
    )
    assert "symbols" not in response.json() and "SOURCE_PRIVATE" not in response.text


@pytest.mark.parametrize(
    "error,status,code",
    [
        (WorkspaceFileError("file_changed", "读取变化"), 409, "file_changed"),
        (
            WorkspaceFileError("file_access_denied", "权限拒绝"),
            403,
            "file_access_denied",
        ),
        (
            WorkspaceFileError("file_read_unsupported", "平台不支持"),
            501,
            "file_read_unsupported",
        ),
        (WorkspaceFileError("file_unavailable", "文件不可用"), 503, "file_unavailable"),
        (RuntimeError("SOURCE_PRIVATE /host/path"), 500, "python_symbols_failed"),
    ],
)
def test_shared_io_error_mapping_is_safe(symbol_api, monkeypatch, error, status, code):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(code_inventory, "read_task_text_file", fail)
    response = get(symbol_api)
    assert response.status_code == status and response.json()["code"] == code
    assert "symbols" not in response.json() and "SOURCE_PRIVATE" not in response.text


def test_symbol_budget_is_coverage_not_error(symbol_api, root, monkeypatch):
    (root / "safe.py").write_text("def first(): pass\ndef second(): pass\n")
    monkeypatch.setattr(service, "MAX_PYTHON_SYMBOLS", 1)
    response = get(symbol_api)
    assert response.status_code == 200
    body = response.json()
    assert len(body["symbols"]) == 1 and body["parsed_files"] == 1
    assert body["truncated"] and body["incomplete_reasons"] == ["symbol_budget"]


def test_no_python_candidates_is_complete_supported_empty_scope(symbol_api, root):
    (root / "safe.py").unlink()
    (root / "client.ts").write_text("const value = 1;\n")
    response = get(symbol_api)
    assert response.status_code == 200
    body = response.json()
    assert body["files"] == body["symbols"] == [] and body["parsed_files"] == 0
    assert body["inspected_files"] == 1 and not body["truncated"]


@pytest.mark.parametrize(
    "query",
    [
        "root=/private",
        "user_id=1",
        "limit=999",
        "path=safe.py",
        "parser=eval",
        "limit=1&limit=2",
    ],
)
def test_input_cannot_override_scope_parser_or_budget(symbol_api, monkeypatch, query):
    monkeypatch.setattr(
        route,
        "scan_python_symbols",
        lambda **kwargs: pytest.fail("invalid input reached scan"),
    )
    response = symbol_api[0].get(symbol_api[1] + "?" + query, headers=HEADERS)
    assert (
        response.status_code == 422
        and response.json()["code"] == "invalid_python_symbols_input"
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        HEADERS | {"X-Local-Runtime-Token": "b" * 64},
        HEADERS | {"Host": "evil.test"},
        HEADERS | {"Origin": "https://evil.test"},
    ],
)
def test_local_access_boundary_precedes_scan(symbol_api, monkeypatch, headers):
    monkeypatch.setattr(
        route,
        "scan_python_symbols",
        lambda **kwargs: pytest.fail("boundary reached scan"),
    )
    assert get(symbol_api, headers=headers).status_code == 403


def test_account_mode_rejected_before_identity(symbol_api, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "account")
    monkeypatch.setattr(
        dependencies, "SessionLocal", lambda: pytest.fail("account identity")
    )
    assert get(symbol_api).json()["code"] == "local_mode_required"


def test_invalid_identifier_has_safe_validation_error(symbol_api):
    response = symbol_api[0].get(
        symbol_api[1].replace("b" * 32, "SOURCE_PRIVATE"), headers=HEADERS
    )
    assert response.status_code == 422 and "SOURCE_PRIVATE" not in response.text
