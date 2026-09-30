"""真实本机身份、隔离PostgreSQL与源码片段HTTP边界；不调用模型。"""

from hashlib import sha256

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import dependencies
from app.config import settings
from app.models import Conversation, Workspace
from app.routers.workspace import code_inventory as route
from app.services.workspace.directory import workspace_path
from app.services.workspace.files import (
    code_inventory,
    python_symbol_search as service,
    python_symbols,
)
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
    project = tmp_path.resolve() / "symbol-search"
    project.mkdir()
    (project / ".gitignore").write_text("ignored.py\n")
    (project / "safe.py").write_bytes(
        b"# PRIVATE_OUTSIDE\r\n@decorate()\r\ndef run():\r\n    return 'BODY_MARKER'\r\n"
    )
    (project / "ignored.py").write_text("PRIVATE[\n")
    (project / ".env").write_text("RUNTIME_PRIVATE")
    return project


@pytest.fixture
def search_api(api):
    return api[0], api[1].replace("/code-inventory", "/python-symbol-search")


def get(api, query="run", **kwargs):
    return api[0].get(
        api[1],
        params={"query": query},
        headers=kwargs.pop("headers", HEADERS),
        **kwargs,
    )


def test_registered_reference_same_file_session_closed_no_side_effects(
    search_api, root, database, engine, monkeypatch
):
    marker = root / "must-not-exist"
    file = root / "safe.py"
    file.write_text(
        f"import nonexistent_module\nopen({str(marker)!r}, 'w').write('PRIVATE')\n@explode()\nasync def run():\n    return 'BODY_MARKER'\n"
    )
    before = snapshot(root)
    data = file.read_bytes()
    digest = sha256(data).hexdigest()
    calls = []
    statements = []
    original = service._parse_python_symbols

    def parse(file, content, **kwargs):
        assert database[0] and all(session.closed for session in database[0])
        calls.append(file.relative_path)
        return original(file, content, **kwargs)

    def capture(connection, cursor, sql, parameters, context, executemany):
        statements.append(sql.lower().lstrip())

    monkeypatch.setattr(service, "_parse_python_symbols", parse)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = get(search_api)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    body = response.json()
    assert body["source"] == "authorized_python_symbol_search"
    assert (
        body["content_trust"] == "untrusted_project_content"
        and body["parser"] == "python_ast_3_12"
    )
    assert (
        body["policy"] == "gitignore_subset_v1"
        and body["query"] == body["normalized_query"] == "run"
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
    assert body["matches"] == [
        {
            "symbol": {
                "relative_path": "safe.py",
                "name": "run",
                "qualified_name": "run",
                "kind": "async_function",
                "start_line": 3,
                "definition_line": 4,
                "end_line": 5,
                "sha256": digest,
            },
            "matched_by": "name",
            "snippet": {
                "text": "@explode()\nasync def run():\n    return 'BODY_MARKER'",
                "start_line": 3,
                "end_line": 5,
                "start_column": 1,
                "end_column": len("    return 'BODY_MARKER'"),
                "truncated": False,
                "incomplete_reasons": [],
            },
        }
    ]
    assert (
        body["searched_files"]
        == body["inspected_files"]
        == body["scanned_directories"]
        == 1
    )
    assert (
        body["examined_symbols"] == body["matched_symbols"] == 1
        and not body["truncated"]
    )
    assert calls == ["safe.py"] and snapshot(root) == before and not marker.exists()
    assert all(value not in response.text for value in [str(root), "PRIVATE", TOKEN])
    assert statements and all(
        sql.startswith("select")
        or ("insert into users" in sql and "on conflict" in sql)
        for sql in statements
    )


@pytest.mark.parametrize(
    "query,count,reason",
    [("run", 2, "name"), ("Outer.run", 1, "qualified_name"), ("RUN", 0, None)],
)
def test_name_and_qualified_name_are_exact_not_substrings(
    search_api, root, query, count, reason
):
    (root / "safe.py").write_text(
        "class Outer:\n    def run(self): pass\ndef run(): pass\n"
    )
    response = get(search_api, query)
    assert response.status_code == 200
    body = response.json()
    assert (
        len(body["matches"]) == body["matched_symbols"] == count
        and not body["truncated"]
    )
    assert all(item["matched_by"] == reason for item in body["matches"])


def test_nfkc_query_is_explicit_in_response(search_api, root):
    (root / "safe.py").write_text("def K(): pass\n")
    response = get(search_api, "K")
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "K" and body["normalized_query"] == "K"
    assert body["matches"][0]["symbol"]["name"] == "K"


@pytest.mark.parametrize(
    "query", ["", "../PRIVATE", "a..b", "x" * 1025, "汉" * 342, " run", "run*"]
)
def test_invalid_query_does_not_scan(search_api, monkeypatch, query):
    monkeypatch.setattr(
        service,
        "_scan_code_inventory",
        lambda **kwargs: pytest.fail("invalid query I/O"),
    )
    response = get(search_api, query)
    assert (
        response.status_code == 422
        and response.json()["code"] == "invalid_python_symbol_query"
    )
    assert "PRIVATE" not in response.text and "matches" not in response.json()


@pytest.mark.parametrize(
    "query_string",
    [
        "",
        "query=run&query=other",
        "query=run&root=/private",
        "query=run&user_id=1",
        "query=run&limit=999",
        "query=run&parser=eval",
        "q=run",
    ],
)
def test_no_missing_duplicate_or_scope_override_parameters(
    search_api, monkeypatch, query_string
):
    monkeypatch.setattr(
        route,
        "search_python_symbols",
        lambda **kwargs: pytest.fail("invalid query reached service"),
    )
    response = search_api[0].get(search_api[1] + "?" + query_string, headers=HEADERS)
    assert (
        response.status_code == 422
        and response.json()["code"] == "invalid_python_symbol_query"
    )


@pytest.mark.parametrize(
    "kind", ["missing-task", "wrong-project", "workspace-owner", "conversation-owner"]
)
def test_ownership_before_filesystem_and_parser(
    search_api, engine, target, monkeypatch, kind
):
    path = search_api[1]
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
        service,
        "_parse_python_symbols",
        lambda *args, **kwargs: pytest.fail("unauthorized parse"),
    )
    response = search_api[0].get(path, params={"query": "run"}, headers=HEADERS)
    assert (
        response.status_code == 404
        and response.json()["code"] == "workspace_not_accessible"
    )


def test_unbound_scope_is_not_cwd(search_api, engine, monkeypatch):
    with Session(engine) as session, session.begin():
        require_value(session.scalar(select(Workspace))).root_path = None
    monkeypatch.setattr(
        workspace_path,
        "_resolve_bound_path",
        lambda **kwargs: pytest.fail("unbound FS"),
    )
    response = get(search_api)
    assert (
        response.status_code == 422
        and response.json()["code"] == "workspace_directory_unbound"
    )


@pytest.mark.parametrize(
    "change", ["binding", "workspace-owner", "conversation-owner", "rule"]
)
def test_final_scope_change_discards_already_collected_snippets(
    search_api, root, engine, target, monkeypatch, change
):
    original = service._parse_python_symbols

    def parse(file, content, **kwargs):
        result = original(file, content, **kwargs)
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

    monkeypatch.setattr(service, "_parse_python_symbols", parse)
    response = get(search_api)
    assert response.status_code == (409 if change in {"binding", "rule"} else 404)
    assert "matches" not in response.json() and "BODY_MARKER" not in response.text


@pytest.mark.parametrize("stage", ["existing-link", "after-resolve"])
def test_links_do_not_supply_reference_text(search_api, root, monkeypatch, stage):
    if stage == "existing-link":
        (root / "link.py").symlink_to(root / "safe.py")
        response = get(search_api)
        assert response.status_code == 200 and len(response.json()["matches"]) == 1
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
        response = get(search_api)
        assert (
            response.status_code == 422
            and response.json()["code"] == "file_not_regular"
        )
        assert "RUNTIME_PRIVATE" not in response.text


@pytest.mark.parametrize(
    "failure", ["syntax", "ignore", "parse-budget", "output-budget"]
)
def test_bad_input_or_budget_is_not_empty_success(
    search_api, root, monkeypatch, failure
):
    if failure == "syntax":
        (root / "safe.py").write_text("def PRIVATE(:\n")
    elif failure == "ignore":
        (root / ".gitignore").write_text("[PRIVATE\n")
    elif failure == "parse-budget":
        monkeypatch.setattr(python_symbols, "MAX_PYTHON_BYTES", 1)
    else:
        monkeypatch.setattr(service, "MAX_SEARCH_RESULT_BYTES", 1)
    response = get(search_api)
    assert (
        response.status_code == 422 and response.headers["cache-control"] == "no-store"
    )
    assert (
        response.json()["code"]
        == {
            "syntax": "python_syntax_invalid",
            "ignore": "unsupported_gitignore",
            "parse-budget": "python_parse_budget_exceeded",
            "output-budget": "python_symbol_search_result_too_large",
        }[failure]
    )
    assert "matches" not in response.json() and "PRIVATE" not in response.text


@pytest.mark.parametrize(
    "error,status,code",
    [
        (WorkspaceFileError("file_changed", "读取变化"), 409, "file_changed"),
        (WorkspaceFileError("file_access_denied", "拒绝"), 403, "file_access_denied"),
        (
            WorkspaceFileError("file_read_unsupported", "不支持"),
            501,
            "file_read_unsupported",
        ),
        (WorkspaceFileError("file_unavailable", "不可用"), 503, "file_unavailable"),
        (RuntimeError("PRIVATE /host/path"), 500, "python_symbol_search_failed"),
    ],
)
def test_fixed_safe_io_errors(search_api, monkeypatch, error, status, code):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(code_inventory, "read_task_text_file", fail)
    response = get(search_api)
    assert response.status_code == status and response.json()["code"] == code
    assert "matches" not in response.json() and "PRIVATE" not in response.text


def test_search_coverage_and_snippet_clipping_are_separate(
    search_api, root, monkeypatch
):
    (root / "safe.py").write_text("def run():\n    return 1\ndef run(): pass\n")
    monkeypatch.setattr(service, "MAX_SYMBOL_MATCHES", 1)
    monkeypatch.setattr(service, "MAX_DEFINITION_LINES", 1)
    response = get(search_api)
    assert response.status_code == 200
    body = response.json()
    assert body["matched_symbols"] == 2 and len(body["matches"]) == 1
    assert body["truncated"] and body["incomplete_reasons"] == ["match_budget"]
    assert body["matches"][0]["snippet"]["incomplete_reasons"] == ["line_budget"]


def test_limited_no_match_keeps_scan_coverage(search_api, root, monkeypatch):
    (root / "safe.py").write_text("def other(): pass\n")
    (root / "child").mkdir()
    (root / "child" / "extra.py").write_text("def run(): pass\n")
    monkeypatch.setattr(code_inventory, "MAX_CODE_DIRECTORIES", 1)
    response = get(search_api)
    assert response.status_code == 200
    body = response.json()
    assert body["matches"] == [] and body["truncated"]
    assert body["incomplete_reasons"] == ["directory_budget"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        HEADERS | {"X-Local-Runtime-Token": "b" * 64},
        HEADERS | {"Host": "evil.test"},
        HEADERS | {"Origin": "https://evil.test"},
    ],
)
def test_local_boundary_before_scan(search_api, monkeypatch, headers):
    monkeypatch.setattr(
        route,
        "search_python_symbols",
        lambda **kwargs: pytest.fail("boundary reached scan"),
    )
    assert get(search_api, headers=headers).status_code == 403


def test_account_mode_rejected_before_identity(search_api, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "account")
    monkeypatch.setattr(
        dependencies, "SessionLocal", lambda: pytest.fail("account identity")
    )
    assert get(search_api).json()["code"] == "local_mode_required"


def test_invalid_identifier_has_safe_validation(search_api):
    response = search_api[0].get(
        search_api[1].replace("b" * 32, "PRIVATE"),
        params={"query": "run"},
        headers=HEADERS,
    )
    assert response.status_code == 422 and "PRIVATE" not in response.text
