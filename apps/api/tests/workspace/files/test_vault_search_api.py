"""搜索 API：真实本机身份、隔离 PostgreSQL、来源及中途失效。"""

from hashlib import sha256

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Conversation, Workspace
from app.routers.workspace import vault as route
from app.services.workspace.files import vault_search
from tests.assertions import require_value
from tests.workspace.files.test_vault_api import (
    HEADERS,
    TOKEN,
    api,
    database,
    get,
    root,
    target,
)


__all__ = ["api", "database", "root", "target"]


def request_search(api, query="Docker", headers=None):
    return get(api, "search", {"query": query}, headers)


def test_registered_search_exact_projection_and_read_only_io(api, root, database, engine, monkeypatch):
    (root / "second.md").write_text("😀Docker Docker\n")
    before = [(p.relative_to(root), p.read_bytes(), p.stat().st_mtime_ns)
              for p in root.rglob("*") if p.is_file()]
    statements = []
    original = vault_search.search_text_content

    def after_close(content, query):
        # 搜索时所有业务查询 Session 已关闭，不跨文本匹配持有事务。
        assert database[0] and all(session.closed for session in database[0])
        return original(content, query)

    def capture(conn, cursor, sql, parameters, context, executemany):
        statements.append(sql.lower().lstrip())

    monkeypatch.setattr(vault_search, "search_text_content", after_close)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = request_search(api)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == {
        "workspace_id", "task_id", "query", "matches", "searched_files",
        "read_bytes", "truncated", "incomplete_reasons",
    }
    assert data["workspace_id"] == "a" * 32 and data["task_id"] == "b" * 32
    assert data["query"] == "Docker" and data["searched_files"] == 2
    assert data["read_bytes"] == sum(len((root / path).read_bytes()) for path in ("note.md", "second.md"))
    assert not data["truncated"] and data["incomplete_reasons"] == []
    assert [m["source"]["relative_path"] for m in data["matches"]] == ["note.md", "second.md"]
    assert [m["column_number"] for m in data["matches"]] == [3, 2]
    for match in data["matches"]:
        assert set(match) == {
            "source", "column_number", "snippet", "snippet_start_column", "snippet_truncated",
        }
        source = match["source"]
        assert set(source) == {"workspace_id", "relative_path", "sha256", "start_line", "end_line"}
        assert source["start_line"] == source["end_line"] == 1
        assert source["sha256"] == sha256((root / source["relative_path"]).read_bytes()).hexdigest()
    assert str(root) not in response.text and TOKEN not in response.text and "PRIVATE" not in response.text
    # 身份依赖仍有幂等用户初始化；Workspace/Task/笔记业务没有写入。
    assert statements and all(
        sql.startswith("select") or ("insert into users" in sql and "on conflict" in sql)
        for sql in statements
    )
    after = [(p.relative_to(root), p.read_bytes(), p.stat().st_mtime_ns)
             for p in root.rglob("*") if p.is_file()]
    assert before == after


@pytest.mark.parametrize("params", [
    None, {"query": ""}, {"query": "x" * 129}, {"query": "a\nb"},
    {"query": "a\rb"}, {"query": "a\x00b"}, {"path": "note.md"},
    [("query", "Docker"), ("query", "Docker")],
    {"query": "Docker", "user_id": "1"}, {"query": "Docker", "limit": "1000"},
])
def test_invalid_query_rejected_before_inventory(api, params, monkeypatch):
    monkeypatch.setattr(vault_search, "list_vault_markdown", lambda **kwargs: pytest.fail("invalid I/O"))
    response = get(api, "search", params)
    assert response.status_code == 422
    assert response.json()["code"] in {"invalid_vault_input", "invalid_search_query"}
    assert response.headers["cache-control"] == "no-store"


def test_complete_no_match_and_budget_incomplete_are_distinct(api, root):
    complete = request_search(api, "absent").json()
    assert complete["matches"] == [] and not complete["truncated"]
    for number in range(20):
        (root / f"{number:02d}.md").write_text("none")
    incomplete = request_search(api, "absent").json()
    assert incomplete["matches"] == [] and incomplete["truncated"]
    assert incomplete["incomplete_reasons"] == ["file_budget"]
    assert incomplete["searched_files"] == 20


def test_snippet_truncation_is_not_search_truncation(api, root):
    (root / "note.md").write_text("a" * 300 + "Docker" + "b" * 300)
    response = request_search(api)
    assert response.status_code == 200
    data = response.json()
    match, = data["matches"]
    assert len(match["snippet"]) == 200 and match["snippet_truncated"]
    assert not data["truncated"] and data["incomplete_reasons"] == []


@pytest.mark.parametrize("kind", ["hidden", "non-markdown", "internal-link", "outside-link"])
def test_search_does_not_expand_markdown_boundary(api, root, kind):
    if kind == "hidden":
        (root / ".obsidian" / "private.md").write_text("PRIVATE")
    elif kind == "non-markdown":
        (root / "private.txt").write_text("PRIVATE")
    elif kind == "internal-link":
        (root / "alias.md").symlink_to(root / ".obsidian" / "config.json")
    else:
        outside = root.parent / "private.md"
        outside.write_text("PRIVATE")
        (root / "alias.md").symlink_to(outside)
    response = request_search(api, "PRIVATE")
    assert response.status_code == 200
    assert response.json()["matches"] == [] and response.json()["searched_files"] == 1
    assert not response.json()["truncated"]


@pytest.mark.parametrize("kind", ["missing-task", "foreign-owner", "foreign-conversation"])
def test_real_ownership_rejected_before_search_io(api, engine, target, monkeypatch, kind):
    path = api[1] + "/search"
    with Session(engine) as session, session.begin():
        if kind == "missing-task":
            path = path.replace(target["task_id"], "f" * 32)
        elif kind == "foreign-owner":
            require_value(session.scalar(select(Workspace))).user_id = target["other_id"]
        else:
            require_value(session.get(Conversation, target["conversation_pk"])).user_id = target["other_id"]
    response = api[0].get(path, params={"query": "Docker"}, headers=HEADERS)
    assert response.status_code == 404 and response.json()["code"] == "workspace_not_accessible"


@pytest.mark.parametrize("kind,status,code", [
    ("deleted", 404, "workspace_path_not_found"),
    ("link", 503, "workspace_path_unavailable"),
    ("foreign-owner", 404, "workspace_not_accessible"),
])
def test_recheck_after_first_hit_does_not_return_partial_success(api, root, engine, target, monkeypatch, kind, status, code):
    second = root / "second.md"
    second.write_text("Docker")
    original = vault_search.read_vault_markdown

    def changed(**kwargs):
        if kwargs["relative_path"] == "second.md":
            if kind == "deleted":
                second.unlink()
            elif kind == "link":
                second.unlink()
                second.symlink_to(root / ".obsidian" / "config.json")
            else:
                with Session(engine) as session, session.begin():
                    require_value(session.scalar(select(Workspace))).user_id = target["other_id"]
        return original(**kwargs)

    monkeypatch.setattr(vault_search, "read_vault_markdown", changed)
    response = request_search(api)
    assert response.status_code == status and response.json()["code"] == code
    assert "matches" not in response.json() and "PRIVATE" not in response.text


@pytest.mark.parametrize("headers", [{}, HEADERS | {"X-Local-Runtime-Token": "b" * 64}])
def test_missing_or_invalid_internal_token_precedes_search(api, monkeypatch, headers):
    monkeypatch.setattr(route, "search_vault_markdown", lambda **kwargs: pytest.fail("boundary first"))
    assert request_search(api, headers=headers).status_code == 403


def test_unknown_search_failure_is_safe_error_not_empty_result(api, monkeypatch):
    def failed(**kwargs):
        raise RuntimeError("PRIVATE/path SQL token")
    monkeypatch.setattr(route, "search_vault_markdown", failed)
    response = request_search(api)
    assert response.status_code == 500 and response.json()["code"] == "vault_read_failed"
    assert "PRIVATE" not in response.text and response.headers["cache-control"] == "no-store"
