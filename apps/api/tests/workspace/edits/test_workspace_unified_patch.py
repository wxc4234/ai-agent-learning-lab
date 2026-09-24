"""单文件补丁的公开契约：精确匹配、有界结果及无外部副作用。"""

import builtins
from dataclasses import FrozenInstanceError
from difflib import unified_diff
from itertools import product
from pathlib import Path
import subprocess

import pytest

from app.services.workspace.edits import workspace_unified_patch as service


HEADER = '--- a/settings.py\n+++ b/settings.py\n'
MARKER = '\\ No newline at end of file\n'
REPLACE = '@@ -1 +1 @@\n-old\n+new\n'


def apply(content='old\n', body=REPLACE, *, path='settings.py'):
    return service.preview_unified_patch(
        content=content, relative_path=path, patch=HEADER + body,
    )


def rejected(content, body, code):
    with pytest.raises(service.UnifiedPatchError) as caught:
        apply(content, body)
    assert caught.value.code == code
    assert str(caught.value) == '补丁未通过校验，未生成修改结果'
    assert not hasattr(caught.value, 'updated_content')


@pytest.mark.parametrize('content,body,expected', [
    ('old\n', REPLACE, 'new\n'),
    ('old\n', '@@ -1,1 +1,1 @@ function name\n-old\n+new\n', 'new\n'),
    ('a\nb\n', '@@ -0,0 +1 @@\n+first\n', 'first\na\nb\n'),
    ('a\nb\n', '@@ -1,0 +2 @@\n+middle\n', 'a\nmiddle\nb\n'),
    ('a\nb\n', '@@ -2,0 +3 @@\n+last\n', 'a\nb\nlast\n'),
    ('', '@@ -0,0 +1 @@\n+first\n', 'first\n'),
    ('a\nb\n', '@@ -1 +0,0 @@\n-a\n', 'b\n'),
    ('a\nb\n', '@@ -2 +1,0 @@\n-b\n', 'a\n'),
    ('a\n', '@@ -1 +0,0 @@\n-a\n', ''),
    ('a\nb\nc\nd\n', '@@ -1 +1,2 @@\n-a\n+A\n+B\n@@ -4 +4,0 @@\n-d\n', 'A\nB\nb\nc\n'),
    ('a\nb\nc\n', '@@ -1 +0,0 @@\n-a\n@@ -3 +2 @@\n-c\n+C\n', 'b\nC\n'),
    ('a\nb\n', '@@ -1 +1 @@\n-a\n+A\n@@ -2 +2 @@\n-b\n+B\n', 'A\nB\n'),
    ('a\n', '@@ -1 +1 @@\n-a\n+a\n' + MARKER, 'a'),
    ('a', '@@ -1 +1 @@\n-a\n' + MARKER + '+a\n', 'a\n'),
    ('a', '@@ -1 +1 @@\n-a\n' + MARKER + '+b\n' + MARKER, 'b'),
    ('a\ntail', '@@ -1,2 +1,2 @@\n-a\n+A\n tail\n' + MARKER, 'A\ntail'),
    ('a\nb\n', '@@ -1,2 +1,3 @@\n a\n+\n b\n', 'a\n\nb\n'),
    ('中文\n', '@@ -1 +1 @@\n-中文\n+😀\n', '😀\n'),
    ('a\u2028b\n', '@@ -1 +1 @@\n-a\u2028b\n+x\u2029y\n', 'x\u2029y\n'),
    ('-- a/other\n', '@@ -1 +1 @@\n--- a/other\n+++ b/other\n', '++ b/other\n'),
])
def test_success_preserves_exact_text_and_metadata(content, body, expected):
    result = apply(content, body)
    assert result.updated_content == expected
    assert result.relative_path == 'settings.py'
    assert result.before_byte_count == len(content.encode('utf-8'))
    assert result.after_byte_count == len(expected.encode('utf-8'))
    assert result.hunk_count == sum(line.startswith('@@') for line in body.split('\n'))
    with pytest.raises(FrozenInstanceError):
        result.updated_content = 'changed'


@pytest.mark.parametrize('content,body,code', [
    ('old\n', '', 'patch_invalid_format'),
    ('old\n', REPLACE[:-1], 'patch_invalid_format'),
    ('old\n', '@@ -0 +1 @@\n-old\n+new\n', 'patch_invalid_format'),
    ('old\n', '@@ -1 +0 @@\n-old\n+new\n', 'patch_invalid_format'),
    ('old\n', '@@ -0,0 +0,0 @@\n', 'patch_invalid_format'),
    ('old\n', '@@ -1000000 +1 @@\n-old\n+new\n', 'patch_invalid_format'),
    ('old\n', '@@ -4001 +1 @@\n-old\n+new\n', 'patch_limit_exceeded'),
    ('old\n', '@@ -1,2 +1 @@\n-old\n+new\n', 'patch_hunk_count_mismatch'),
    ('old\n', REPLACE + '+extra\n', 'patch_hunk_count_mismatch'),
    ('old\n', REPLACE + 'diff --git a/x b/x\n', 'patch_invalid_format'),
    ('old\n', REPLACE + '--- a/other\n+++ b/other\n', 'patch_hunk_count_mismatch'),
    ('old\n', REPLACE + '\n', 'patch_invalid_format'),
    ('old\n', '@@ -1 +1 @@\n?old\n+new\n', 'patch_invalid_format'),
    ('old\n', '@@ -1 +1 @@\n' + MARKER, 'patch_invalid_format'),
    ('\n', '@@ -1 +1 @@\n-\n' + MARKER + '+new\n', 'patch_invalid_format'),
    ('a', '@@ -1 +1 @@\n-a\n' + MARKER * 2 + '+b\n', 'patch_invalid_format'),
    ('a\nb\n', '@@ -1,2 +1 @@\n-a\n' + MARKER + '-b\n+c\n', 'patch_invalid_format'),
    ('a\nb\n', '@@ -1 +1 @@\n-a\n+A\n' + MARKER, 'patch_invalid_format'),
    ('a', '@@ -1,0 +2 @@\n+b\n', 'patch_invalid_format'),
    ('old\n', '@@ -2 +2 @@\n-old\n+new\n', 'patch_range_out_of_bounds'),
    ('old\n', '@@ -2,0 +3 @@\n+new\n', 'patch_range_out_of_bounds'),
    ('old\n', '@@ -1 +2 @@\n-old\n+new\n', 'patch_new_position_mismatch'),
    ('OLD\n', REPLACE, 'patch_context_mismatch'),
    ('old', REPLACE, 'patch_context_mismatch'),
    ('secret\nold\n', REPLACE, 'patch_context_mismatch'),
    ('old\n', '@@ -1 +1 @@\n old\n', 'patch_no_change'),
    ('old\n', '@@ -1 +1 @@\n-old\n+old\n', 'patch_no_change'),
    ('old\n', REPLACE + '@@ -1 +1 @@\n-old\n+again\n', 'patch_hunks_overlap'),
    ('a\nb\n', '@@ -2 +2 @@\n-b\n+B\n@@ -1 +1 @@\n-a\n+A\n', 'patch_hunks_overlap'),
    ('a\n', '@@ -0,0 +1 @@\n+x\n@@ -0,0 +2 @@\n+y\n', 'patch_hunks_overlap'),
    ('a\nb\n', '@@ -1,2 +1 @@\n-a\n-b\n+A\n@@ -2 +2 @@\n-b\n+B\n', 'patch_hunks_overlap'),
    ('a\nb\n', '@@ -1 +1,2 @@\n-a\n+A\n+extra\n@@ -2 +2 @@\n-b\n+B\n', 'patch_new_position_mismatch'),
])
def test_rejected_patch_is_fixed_error_without_partial_candidate(content, body, code):
    rejected(content, body, code)


@pytest.mark.parametrize('field', ['content', 'patch', 'relative_path'])
@pytest.mark.parametrize('value', [None, 1, [], 'a\x00b', '\ud800', 'a\r\nb', 'a\rb'])
def test_invalid_text_is_rejected_in_each_input(field, value):
    arguments = {'content': 'old\n', 'patch': HEADER + REPLACE, 'relative_path': 'settings.py'}
    arguments[field] = value
    with pytest.raises(service.UnifiedPatchError) as caught:
        service.preview_unified_patch(**arguments)
    assert caught.value.code == 'patch_invalid_text'


@pytest.mark.parametrize('path', [
    '', '.', '..', '../x', 'a/../x', './x', 'a//x', 'a/', '/x',
    'C:/x', 'C:x', '\\x', 'a\\x', 'a:b', 'a\nb', 'a\tb', 'a\x7fb',
    'a.', 'a ', 'CON', 'nul.txt', 'a/COM1', 'a?b', 'a*b',
])
def test_invalid_paths_are_not_normalized(path):
    with pytest.raises(service.UnifiedPatchError) as caught:
        apply(path=path)
    assert caught.value.code == 'patch_invalid_path'


@pytest.mark.parametrize('headers', [
    '--- a/other.py\n+++ b/other.py\n',
    '--- a/settings.py\n+++ b/renamed.py\n',
    '--- /dev/null\n+++ b/settings.py\n',
    '--- a/settings.py\n+++ /dev/null\n',
    '--- a/settings.py\t2026-09-24\n+++ b/settings.py\n',
    'diff --git a/settings.py b/settings.py\n' + HEADER,
])
def test_target_and_supported_dialect_are_strict(headers):
    with pytest.raises(service.UnifiedPatchError) as caught:
        service.preview_unified_patch(content='old\n', relative_path='settings.py', patch=headers + REPLACE)
    assert caught.value.code == 'patch_target_mismatch'


@pytest.mark.parametrize('path', ['目录/文件.py', 'dir with space/file name.txt'])
def test_safe_non_ascii_and_space_paths(path):
    result = service.preview_unified_patch(
        content='old\n', relative_path=path,
        patch=f'--- a/{path}\n+++ b/{path}\n' + REPLACE,
    )
    assert result.relative_path == path and result.updated_content == 'new\n'


def test_later_conflict_never_constructs_partial_candidate(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail('candidate must not be constructed before all hunks pass')
    monkeypatch.setattr(service, 'TextPatchCandidate', forbidden)
    rejected('a\nb\n', '@@ -1 +1 @@\n-a\n+A\n@@ -2 +2 @@\n-wrong\n+B\n', 'patch_context_mismatch')


def test_pure_memory_path_has_no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure patch operation must not access external resources')
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert apply().updated_content == 'new\n'


def test_real_byte_budget_accepts_boundary_and_rejects_growth():
    content = 'x' * (service.MAX_EDIT_TEXT_BYTES - 1) + '\n'
    # 不复制整段原文到补丁中；只在末尾插入，检查整体候选预算。
    rejected(content, '@@ -1,0 +2 @@\n+y\n', 'patch_limit_exceeded')
    shortened = content[:-2] + '\n'
    candidate = apply(shortened, '@@ -1,0 +2 @@\n+\n')
    assert candidate.after_byte_count == service.MAX_EDIT_TEXT_BYTES
    rejected('x' * (service.MAX_EDIT_TEXT_BYTES + 1), REPLACE, 'patch_limit_exceeded')
    rejected('中' * (service.MAX_EDIT_TEXT_BYTES // 3 + 1), REPLACE, 'patch_limit_exceeded')


@pytest.mark.parametrize('field,limit', [('patch', service.MAX_PATCH_BYTES), ('relative_path', service.MAX_PATH_BYTES)])
def test_independent_input_byte_budgets(field, limit):
    arguments = {'content': 'old\n', 'patch': HEADER + REPLACE, 'relative_path': 'settings.py'}
    arguments[field] = '中' * (limit // 3 + 1)
    with pytest.raises(service.UnifiedPatchError) as caught:
        service.preview_unified_patch(**arguments)
    assert caught.value.code == 'patch_limit_exceeded'


def test_line_count_limits_cover_original_result_and_patch(monkeypatch):
    monkeypatch.setattr(service, 'MAX_PREVIEW_LINES', 2)
    rejected('a\nb\nc\n', REPLACE, 'patch_limit_exceeded')
    rejected('a\nb\n', '@@ -0,0 +1 @@\n+x\n', 'patch_limit_exceeded')
    rejected('a\n', '@@ -1 +1,3 @@\n-a\n+x\n+y\n+z\n', 'patch_limit_exceeded')
    monkeypatch.setattr(service, 'MAX_PATCH_LINES', 4)
    rejected('old\n', REPLACE, 'patch_limit_exceeded')


def test_hunk_budget_boundary(monkeypatch):
    body = '@@ -1 +1 @@\n-a\n+A\n@@ -3 +3 @@\n-c\n+C\n'
    monkeypatch.setattr(service, 'MAX_PATCH_HUNKS', 2)
    assert apply('a\nb\nc\n', body).hunk_count == 2
    monkeypatch.setattr(service, 'MAX_PATCH_HUNKS', 1)
    rejected('a\nb\nc\n', body, 'patch_limit_exceeded')


def test_generated_diffs_roundtrip_against_independent_expected_text():
    # difflib 仅生成输入，不计算被测结果；预期来自生成前的目标字符串。
    # 包含重复行、空行、符号前缀及多块编辑，不使用被测解析器生成测试数据。
    texts = ['', 'tail', 'a\ntail', 'a\n\nend', '中\n😀\n']
    texts += [''.join(parts) for length in range(1, 4) for parts in product(('a\n', 'b\n', '\n'), repeat=length)]
    texts += ['a\nb\nc\nd\ne\nf\ng\n', 'A\nb\nc\nd\ne\nf\nG\n']
    checked = 0
    for before in texts:
        for after in texts:
            if before == after:
                continue
            pieces = unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile='a/settings.py', tofile='b/settings.py', n=1,
            )
            patch = ''.join(piece if piece.endswith('\n') else piece + '\n' + MARKER for piece in pieces)
            candidate = service.preview_unified_patch(content=before, relative_path='settings.py', patch=patch)
            assert candidate.updated_content == after
            checked += 1
    assert checked > 1500
