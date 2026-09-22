"""纯内存编辑预览：精确定位、原始换行与展示预算。"""

import builtins
import json
from dataclasses import FrozenInstanceError

import pytest

from app.services.workspace.edits import workspace_edit_preview as service


def preview(content="hello world", old="world", new="reader"):
    return service.preview_text_replacement(content=content, old_text=old, new_text=new)


@pytest.mark.parametrize("content,old,new,expected", [
    ("hello world", "world", "reader", "hello reader"),
    ("head-middle-tail", "head", "start", "start-middle-tail"),
    ("head-middle-tail", "middle", "", "head--tail"),
    ("all", "all", "", ""),
    (" 中 文 ", "中", "😀", " 😀 文 "),
    ("abc\ndef", "c\nd", "X\r\nY", "abX\r\nYef"),
    ("a.*b", ".*", "[x]", "a[x]b"),
])
def test_exact_replacement_and_byte_counts(content, old, new, expected):
    result = preview(content, old, new)
    assert result.updated_content == expected
    assert result.before_byte_count == len(content.encode("utf-8"))
    assert result.after_byte_count == len(expected.encode("utf-8"))
    assert result.diff.startswith("--- before\n+++ after\n@@ ")
    assert not result.diff_truncated
    with pytest.raises(FrozenInstanceError):
        result.updated_content = "mutated"


@pytest.mark.parametrize("field", ["content", "old_text", "new_text"])
@pytest.mark.parametrize("value", [None, True, 123, [], "a\x00b", chr(0xD800), chr(0xDFFF)])
def test_invalid_text_in_each_field(field, value):
    values = {"content": "target", "old_text": "target", "new_text": "next", field: value}
    with pytest.raises(service.EditPreviewError) as caught:
        service.preview_text_replacement(**values)
    assert caught.value.code == "invalid_edit_text"


@pytest.mark.parametrize("content,old,new,code", [
    ("abc", "", "x", "empty_old_text"),
    ("abc", "b", "b", "edit_no_change"),
    ("", "x", "y", "edit_target_not_found"),
    ("ABC", "abc", "x", "edit_target_not_found"),
    ("a a", "a", "x", "edit_target_ambiguous"),
    ("aaa", "aa", "x", "edit_target_ambiguous"),
    ("ababa", "aba", "x", "edit_target_ambiguous"),
])
def test_rejection_does_not_generate_diff(monkeypatch, content, old, new, code):
    monkeypatch.setattr(service, "_build_review_diff", lambda **kwargs: pytest.fail("no diff on rejected edit"))
    with pytest.raises(service.EditPreviewError) as caught:
        preview(content, old, new)
    assert caught.value.code == code


@pytest.mark.parametrize("ending", ["\n", "\r", "\r\n", ""])
def test_line_endings_and_eof_preserved(ending):
    content = f"first{ending}second{ending}"
    result = preview(content, "second", "changed")
    assert result.updated_content == f"first{ending}changed{ending}"
    assert "\r" not in result.diff
    displayed = json.dumps(f"changed{ending}" if ending else "firstchanged", ensure_ascii=False)
    assert displayed in result.diff


@pytest.mark.parametrize("content,expected", [
    ("", []), ("\n", ["\n"]), ("\r\n", ["\r\n"]),
    ("a\r\nb\rc\nd", ["a\r\n", "b\r", "c\n", "d"]),
    ("a\t\"\\b\x1bc", ['a\t"\\b\x1bc']),
    ("a\u2028b\u2029c", ["a\u2028b\u2029c"]),
])
def test_review_representation_roundtrips_raw_lines(content, expected):
    lines = service._review_lines(content)
    assert [json.loads(line) for line in lines] == expected
    assert "".join(json.loads(line) for line in lines) == content


@pytest.mark.parametrize("before,after", [("x\n", "x"), ("x", "x\n"), ("x\r\n", "x\n")])
def test_newline_only_changes_are_visible(before, after):
    result = preview(before, before, after)
    assert result.updated_content == after
    assert f'-{json.dumps(before)}\n' in result.diff
    assert f'+{json.dumps(after)}\n' in result.diff


@pytest.mark.parametrize("field", ["content", "old_text", "new_text"])
@pytest.mark.parametrize("text", ["x" * (service.MAX_EDIT_TEXT_BYTES + 1), "😀" * (service.MAX_EDIT_TEXT_BYTES // 4 + 1)])
def test_byte_limit_on_each_input(field, text):
    args = {"content": "x", "old_text": "x", "new_text": "y", field: text}
    with pytest.raises(service.EditPreviewError) as caught:
        service.preview_text_replacement(**args)
    assert caught.value.code == "edit_text_too_large"


def test_exact_byte_limit_and_updated_content_overflow():
    exact = "😀" * (service.MAX_EDIT_TEXT_BYTES // 4)
    assert service._validate_text(exact) == service.MAX_EDIT_TEXT_BYTES
    result = preview(exact, exact, "")
    assert result.updated_content == ""
    assert result.diff_truncated
    content = "!" + "x" * (service.MAX_EDIT_TEXT_BYTES - 1)
    assert preview(content, "!", "?").after_byte_count == service.MAX_EDIT_TEXT_BYTES
    with pytest.raises(service.EditPreviewError) as caught:
        preview(content, "!", "??")
    assert caught.value.code == "edit_text_too_large"


@pytest.mark.parametrize("terminal_newline", [True, False])
def test_exact_line_limit(terminal_newline):
    content = "x\n" * (service.MAX_PREVIEW_LINES - 1) + "last" + ("\n" if terminal_newline else "")
    assert preview(content, "last", "next").updated_content.endswith("next\n" if terminal_newline else "next")


@pytest.mark.parametrize("side", ["before", "after"])
def test_line_overflow(side):
    many = "x\n" * (service.MAX_PREVIEW_LINES + 1)
    with pytest.raises(service.EditPreviewError) as caught:
        preview(many, many, "short") if side == "before" else preview("short", "short", many)
    assert caught.value.code == "edit_preview_too_many_lines"


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_diff_character_boundary(monkeypatch, offset):
    complete = preview("甲", "甲", "乙").diff
    limit = len(complete) + offset
    monkeypatch.setattr(service, "MAX_DIFF_CHARACTERS", limit)
    result = preview("甲", "甲", "乙")
    assert result.diff == complete[:limit]
    assert result.diff_truncated is (offset < 0)
    assert result.updated_content == "乙"


def test_default_diff_limit_and_complete_updated_text():
    before = "a" * 20_000
    after = "b" * 20_000
    result = preview(before, before, after)
    assert len(result.diff) == service.MAX_DIFF_CHARACTERS
    assert result.diff_truncated
    assert result.updated_content == after


def test_diff_boundary_between_generated_pieces(monkeypatch):
    monkeypatch.setattr(service, "MAX_DIFF_CHARACTERS", len("--- before\n"))
    result = preview()
    assert result.diff == "--- before\n"
    assert result.diff_truncated


def test_pure_repeatable_without_file_access(monkeypatch):
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: pytest.fail("no file access"))
    first = preview()
    assert preview() == first
    assert preview("other", "other", "next").updated_content == "next"
    assert preview() == first
