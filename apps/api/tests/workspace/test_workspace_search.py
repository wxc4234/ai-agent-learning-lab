"""单文件字面量搜索：定位与输出预算、错误传播和少量真实读取集成。"""

from dataclasses import FrozenInstanceError

import pytest

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.workspace import workspace_search as service
from app.services.workspace.workspace_file import WorkspaceFileError, WorkspaceTextFile
from app.services.workspace.workspace_path import WorkspacePathError
from tests.workspace import test_workspace_path as path_tests


root = path_tests.root
target = path_tests.target
database = path_tests.database


def search(query="needle"):
    return service.search_task_text_file(user_id=1, workspace_id="w", task_id="t", relative_path="./notes.txt", query=query)


@pytest.mark.parametrize("query", [None, 1, True, [], {}, "", "x" * 129, "a\nb", "a\rb", "a\x00b"])
def test_invalid_query_precedes_io(monkeypatch, query):
    monkeypatch.setattr(service, "read_task_text_file", lambda **kwargs: pytest.fail("must not read"))
    with pytest.raises(service.WorkspaceSearchError) as caught:
        search(query)
    assert caught.value.code == "invalid_search_query"


@pytest.mark.parametrize("query", [" ", " x ", "中" * 128, ".*", "😀"])
def test_valid_query_preserved_and_identity_forwarded(monkeypatch, query):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return WorkspaceTextFile("notes.txt", query, len(query.encode()))

    monkeypatch.setattr(service, "read_task_text_file", read)
    result = search(query)
    assert calls == [{"user_id": 1, "workspace_id": "w", "task_id": "t", "relative_path": "./notes.txt"}]
    assert result.relative_path == "notes.txt" and result.query == query
    assert result.matches[0].snippet == query
    assert result.matches[0].column_number == 1
    with pytest.raises(FrozenInstanceError):
        result.truncated = True
    with pytest.raises(FrozenInstanceError):
        result.matches[0].line_number = 99


@pytest.mark.parametrize("content,query,positions", [
    ("", "x", []), ("abc\n", "x", []),
    ("FastAPI fastapi\nfastapi", "FastAPI", [(1, 1)]),
    ("foo foo foo\nfoo", "foo", [(1, 1), (2, 1)]),
    ("abc\n.*", ".*", [(2, 1)]),
    ("a\r\nx\ry\nx", "x", [(2, 1), (4, 1)]),
    ("\n\n😀中文needle", "needle", [(3, 4)]),
    ("a\u2028needle", "needle", [(1, 3)]),
    ("e\u0301needle", "needle", [(1, 3)]),
])
def test_literal_case_first_occurrence_and_line_rules(content, query, positions):
    matches, truncated = service._search_content(content, query)
    assert [(match.line_number, match.column_number) for match in matches] == positions
    assert not truncated


@pytest.mark.parametrize("count", [0, 49, 50, 51, 100])
def test_matching_line_limit_not_occurrence_count(count):
    content = "\n".join("needle needle\nunmatched" for _ in range(count))
    matches, truncated = service._search_content(content, "needle")
    assert len(matches) == min(count, 50)
    assert [match.line_number for match in matches] == list(range(1, 2 * min(count, 50), 2))
    assert truncated is (count > 50)


@pytest.mark.parametrize("prefix,suffix", [(0, 0), (0, 300), (40, 32), (41, 0), (300, 300)])
def test_long_query_fully_present_with_accurate_snippet_coordinates(prefix, suffix):
    query = "中" * 128
    line = "a" * prefix + query + "b" * suffix
    matches, truncated = service._search_content(line, query)
    match = matches[0]
    assert match.column_number == prefix + 1
    assert len(match.snippet) <= 200 and query in match.snippet
    start = match.snippet_start_column - 1
    assert match.snippet == line[start:start + 200]
    assert match.snippet_truncated is (start > 0 or start + len(match.snippet) < len(line))
    assert not truncated


def test_text_output_budget_and_independent_truncation_flags():
    content = "\n".join("a" * 300 + "needle" + "b" * 300 for _ in range(51))
    matches, truncated = service._search_content(content, "needle")
    assert truncated and all(match.snippet_truncated for match in matches)
    assert sum(len(match.snippet) for match in matches) == 10_000


@pytest.mark.parametrize("error", [
    WorkspaceNotAccessibleError(), WorkspacePathError("workspace_directory_unbound", "safe"),
    WorkspaceFileError("file_not_utf8_text", "safe"), WorkspaceFileError("file_too_large", "safe"),
    RuntimeError("database failure"),
])
def test_read_errors_propagate_without_becoming_empty_results(monkeypatch, error):
    def fail(**kwargs):
        raise error

    monkeypatch.setattr(service, "read_task_text_file", fail)
    with pytest.raises(type(error)) as caught:
        search()
    assert caught.value is error


@pytest.mark.parametrize("kind", ["success", "foreign", "non-utf8"])
def test_real_authorized_read_boundary(database, target, root, monkeypatch, kind):
    path = root / "notes.txt"
    path.write_bytes(b"\xff" if kind == "non-utf8" else "标题\r\n这里有needle\n".encode())
    args = {key: target[key] for key in ("user_id", "workspace_id", "task_id")}
    if kind == "foreign":
        args["user_id"] = target["other_id"]
    original = service._search_content

    def after_close(content, query):
        assert all(session.closed for session in database[0])
        return original(content, query)

    monkeypatch.setattr(service, "_search_content", after_close)
    if kind != "success":
        error = WorkspaceNotAccessibleError if kind == "foreign" else WorkspaceFileError
        with pytest.raises(error):
            service.search_task_text_file(**args, relative_path="notes.txt", query="needle")
    else:
        result = service.search_task_text_file(**args, relative_path="notes.txt", query="needle")
        assert result.matches == (service.WorkspaceSearchMatch(2, 4, "这里有needle", 1, False),)
        assert not result.truncated
    assert database[1] and all(sql.lstrip().upper().startswith("SELECT") for sql in database[1])
