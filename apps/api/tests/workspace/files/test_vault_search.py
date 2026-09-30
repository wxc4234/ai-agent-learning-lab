"""跨文件搜索：真实临时 Markdown、独立内容核对与全局预算。"""

from hashlib import sha256

import pytest

from app.services.workspace.files import vault_search, workspace_search
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.services.workspace.files.workspace_listing import WorkspaceListingError
from tests.workspace.files.test_vault import notes


__all__ = ["notes"]


def search(query="needle"):
    return vault_search.search_vault_markdown(
        user_id=1, workspace_id="a" * 32, task_id="b" * 32, query=query,
    )


def test_cross_file_sources_are_exact_and_ordered(notes):
    first = "标题\r\n😀中文needle needle\n不匹配\r末行needle".encode()
    second = b"needle\n"
    (notes / "b.md").write_bytes(second)
    (notes / "a.md").write_bytes(first)
    result = search()
    assert result.workspace_id == "a" * 32 and result.task_id == "b" * 32
    assert result.query == "needle" and result.searched_files == 2
    assert result.read_bytes == len(first) + len(second)
    assert not result.truncated and result.incomplete_reasons == ()
    assert [(m.source.relative_path, m.source.start_line, m.column_number) for m in result.matches] == [
        ("a.md", 2, 4), ("a.md", 4, 3), ("b.md", 1, 1),
    ]
    for match in result.matches:
        data = first if match.source.relative_path == "a.md" else second
        lines = data.decode().replace("\r\n", "\n").replace("\r", "\n").split("\n")
        assert match.source.workspace_id == "a" * 32
        assert match.source.end_line == match.source.start_line
        assert match.source.sha256 == sha256(data).hexdigest()
        assert match.source.start_line is not None
        line = lines[match.source.start_line - 1]
        start = match.snippet_start_column - 1
        assert match.snippet == line[start:start + len(match.snippet)]
        assert line[match.column_number - 1:].startswith("needle")


@pytest.mark.parametrize("query,expected", [("Docker", 1), ("docker", 0), (".*", 1), (" ", 1)])
def test_literal_case_and_whitespace_rules(notes, query, expected):
    (notes / "note.md").write_text("Docker Docker .*\n")
    result = search(query)
    assert len(result.matches) == expected and result.query == query
    assert not result.truncated


@pytest.mark.parametrize("content", ["", "no match", "no match\n"])
def test_complete_no_match(notes, content):
    (notes / "note.md").write_text(content)
    result = search()
    assert result.matches == () and result.searched_files == 1
    assert not result.truncated


def test_empty_vault_has_complete_empty_result(notes):
    result = search()
    assert result.matches == () and result.searched_files == result.read_bytes == 0
    assert not result.truncated


@pytest.mark.parametrize("query", [None, 1, "", "x" * 129, "a\nb", "a\rb", "a\x00b"])
def test_invalid_query_precedes_inventory(monkeypatch, query):
    monkeypatch.setattr(vault_search, "list_vault_markdown", lambda **kwargs: pytest.fail("invalid query I/O"))
    with pytest.raises(workspace_search.WorkspaceSearchError):
        search(query)
    with pytest.raises(workspace_search.WorkspaceSearchError):
        workspace_search.search_text_content("anything", query)


def test_public_matcher_is_pure_and_unicode_line_separator_is_literal(monkeypatch):
    monkeypatch.setattr(workspace_search, "read_task_text_file", lambda **kwargs: pytest.fail("pure matcher I/O"))
    matches, truncated = workspace_search.search_text_content("a\u2028needle", "needle")
    assert len(matches) == 1 and matches[0].line_number == 1
    assert matches[0].column_number == 3 and not truncated


def test_long_line_citation_contains_whole_query_and_only_snippet_is_cut(notes):
    query = "中" * 128
    line = "a" * 300 + query + "b" * 300
    (notes / "note.md").write_text(line)
    result = search(query)
    match, = result.matches
    assert match.column_number == 301 and match.snippet_start_column == 261
    assert match.snippet == line[260:460] and query in match.snippet
    assert len(match.snippet) == 200 and match.snippet_truncated
    assert not result.truncated


@pytest.mark.parametrize("count", [20, 21])
def test_file_budget_checks_before_next_read(notes, monkeypatch, count):
    for number in range(count):
        (notes / f"{number:02d}.md").write_text("none")
    calls = []
    original = vault_search.read_vault_markdown

    def recorded(**kwargs):
        calls.append(kwargs["relative_path"])
        return original(**kwargs)

    monkeypatch.setattr(vault_search, "read_vault_markdown", recorded)
    result = search()
    assert result.matches == () and result.searched_files == min(count, 20)
    assert len(calls) == min(count, 20) and result.read_bytes == min(count, 20) * 4
    assert result.truncated is (count > 20)
    assert result.incomplete_reasons == (("file_budget",) if count > 20 else ())


@pytest.mark.parametrize("spread,count", [(False, 50), (False, 51), (True, 50), (True, 51)])
def test_exact_and_excess_match_budget_single_and_cross_file(notes, spread, count):
    if spread:
        (notes / "a.md").write_text("needle\n" * 25)
        (notes / "b.md").write_text("needle\n" * (count - 25))
    else:
        (notes / "a.md").write_text("needle\n" * count)
    result = search()
    assert len(result.matches) == 50
    assert result.truncated is (count > 50)
    assert result.incomplete_reasons == (("match_budget",) if count > 50 else ())


def test_exact_matches_then_unmatched_file_can_still_be_complete(notes):
    (notes / "a.md").write_text("needle\n" * 50)
    (notes / "b.md").write_text("none")
    result = search()
    assert len(result.matches) == 50 and result.searched_files == 2
    assert not result.truncated


def test_extra_match_stops_before_later_file(notes, monkeypatch):
    (notes / "a.md").write_text("needle\n" * 51)
    (notes / "b.md").write_text("needle")
    original = vault_search.read_vault_markdown

    def first_only(**kwargs):
        assert kwargs["relative_path"] == "a.md"
        return original(**kwargs)

    monkeypatch.setattr(vault_search, "read_vault_markdown", first_only)
    result = search()
    assert len(result.matches) == 50 and result.searched_files == 1
    assert result.incomplete_reasons == ("match_budget",)


def test_empty_matches_with_truncated_inventory_are_incomplete(notes):
    for number in range(201):
        (notes / f"{number:03d}.txt").touch()
    result = search()
    assert result.matches == () and result.searched_files == 0
    assert result.truncated and result.incomplete_reasons == ("inventory_truncated",)


def test_inventory_and_read_budget_reasons_are_both_preserved(notes):
    for number in range(101):
        (notes / f"{number:03d}.md").write_text("none")
    result = search()
    assert result.searched_files == 20 and result.matches == ()
    assert result.incomplete_reasons == ("inventory_truncated", "file_budget")


@pytest.mark.parametrize("kind,code", [
    ("deleted", "file_not_found"), ("binary", "file_not_utf8_text"),
])
def test_failure_after_first_match_is_not_returned_as_partial_success(notes, monkeypatch, kind, code):
    (notes / "a.md").write_text("needle")
    second = notes / "b.md"
    second.write_text("needle")
    original = vault_search.read_vault_markdown

    def changed(**kwargs):
        if kwargs["relative_path"] == "b.md":
            if kind == "deleted":
                second.unlink()
            else:
                second.write_bytes(b"\xff")
        return original(**kwargs)

    monkeypatch.setattr(vault_search, "read_vault_markdown", changed)
    with pytest.raises(WorkspaceFileError) as caught:
        search()
    assert caught.value.code == code


def test_inventory_failure_is_not_empty_success(monkeypatch):
    def failed(**kwargs):
        raise WorkspaceListingError("directory_listing_changed", "目录发生变化")
    monkeypatch.setattr(vault_search, "list_vault_markdown", failed)
    with pytest.raises(WorkspaceListingError):
        search()


def test_fresh_search_reflects_changed_content_and_digest(notes):
    note = notes / "note.md"
    note.write_text("needle")
    old = search().matches[0]
    note.write_text("first\nnew needle")
    new = search().matches[0]
    assert new.source.start_line == 2 and new.column_number == 5
    assert new.source.sha256 == sha256(note.read_bytes()).hexdigest()
    assert new.source.sha256 != old.source.sha256 and new.snippet == "new needle"
