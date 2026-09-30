"""纯忽略语义与受控 Git 对照；不运行任何用户仓库配置。"""

import os
import subprocess
from pathlib import PurePosixPath

import pytest

from app.services.workspace.files.code_ignore import (
    CodeIgnoreError,
    ignored_by_rules,
    parse_code_ignore,
)


@pytest.mark.parametrize(
    "pattern,path,directory,expected",
    [
        ("*.py", "src/a.py", False, True),
        ("*.py", "a.ts", False, False),
        ("/a.py", "a.py", False, True),
        ("/a.py", "src/a.py", False, False),
        ("src/a.py", "src/a.py", False, True),
        ("src/a.py", "other/src/a.py", False, False),
        ("cache/", "src/cache", True, True),
        ("cache/", "cache", False, False),
        ("/cache/", "cache", True, True),
        ("/cache/", "src/cache", True, False),
        ("a?b.py", "a_b.py", False, True),
        ("a?b.py", "a中文b.py", False, False),
        ("*.py[cod]", "a.pyc", False, True),
        ("*.py[cod]", "a.py", False, False),
        ("[a-z].py", "b.py", False, True),
        ("[!ab].py", "c.py", False, True),
        ("[!ab].py", "a.py", False, False),
        ("**/a.py", "a.py", False, True),
        ("**/a.py", "src/deep/a.py", False, True),
        ("a/**/b.py", "a/b.py", False, True),
        ("a/**/b.py", "a/deep/b.py", False, True),
        ("a/**", "a", True, False),
        ("a/**", "a/b.py", False, True),
        ("a/**/", "a/deep", True, True),
        ("a/**/", "a/b.py", False, False),
        ("*", "a/deep", True, True),
        (r"\#a.py", "#a.py", False, True),
        (r"\!a.py", "!a.py", False, True),
        (" a.py", " a.py", False, True),
        ("a.py  ", "a.py", False, True),
        ("A.py", "a.py", False, False),
        ("#comment\n\n", "anything", False, False),
        ("中文*.py", "src/中文名.py", False, True),
        ("a/b", "a/b", True, True),
    ],
)
def test_supported_pattern_semantics(pattern, path, directory, expected):
    rules = parse_code_ignore(pattern)
    assert (
        ignored_by_rules(PurePosixPath(path).parts, is_directory=directory, rules=rules)
        is expected
    )


def test_order_negation_and_nested_scope():
    root = parse_code_ignore("*.py\n!keep.py\nkeep.py\n")
    nested = parse_code_ignore("!keep.py\n", base=("src",))
    assert ignored_by_rules(("keep.py",), is_directory=False, rules=root + nested)
    assert not ignored_by_rules(
        ("src", "keep.py"), is_directory=False, rules=root + nested
    )
    assert ignored_by_rules(
        ("src", "other.py"), is_directory=False, rules=root + nested
    )
    assert parse_code_ignore("a.py\r\n!b.py\r\n") == parse_code_ignore("a.py\n!b.py\n")


@pytest.mark.parametrize(
    "pattern",
    [
        "!",
        "/",
        "//",
        "a//b",
        ".",
        "a/../b",
        "**foo",
        "a/**b/c",
        "[",
        "[]",
        "[!]",
        "[z-a]",
        "[a--b]",
        "[[:digit:]]",
        "[中文]",
        "[^a]",
        "a]",
        r"a\ b",
        r"a\*b",
        "\ufeff*.py",
        "a\x00b",
        "a\tb",
        "a\rb",
        "a/" * 16 + "b",
        "x" * 1025,
    ],
)
def test_unknown_syntax_is_a_failure_not_an_empty_rule(pattern):
    with pytest.raises(CodeIgnoreError) as caught:
        parse_code_ignore(pattern)
    assert caught.value.code == "unsupported_gitignore"
    assert pattern not in str(caught.value)


def test_exact_rule_budgets():
    assert len(parse_code_ignore("x\n" * 200)) == 200
    with pytest.raises(CodeIgnoreError):
        parse_code_ignore("x\n" * 201)
    assert parse_code_ignore("#" + "x" * 16383) == ()
    with pytest.raises(CodeIgnoreError):
        parse_code_ignore("#" + "x" * 16384)
    assert parse_code_ignore("x" * 1024)


@pytest.mark.parametrize(
    "pattern",
    [
        "*.py\n!src/keep.py",
        "src/**\n!src/keep.py",
        "**/nested/*.py",
        "a/**/b.py",
        "[a-z].py",
        "*.py[cod]",
        "[!ab].py",
        "a?b.py",
        r"\#a.py",
        "中文*.py",
        "src/**/",
        "src/**/keep.py",
        "/a.py",
        "src/*",
        "a**b.py",
    ],
)
def test_supported_rules_match_trusted_git_fixture(tmp_path, pattern):
    # a**b是合法Git语法但属于本课明确拒绝的子集之外，单独验证拒绝即可。
    if "a**b" in pattern:
        with pytest.raises(CodeIgnoreError):
            parse_code_ignore(pattern)
        return
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    subprocess.run(
        ["git", "init", "-q"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
    (tmp_path / ".gitignore").write_text(pattern, encoding="utf-8")
    paths = (
        "a.py",
        "b.py",
        "c.py",
        "a.pyc",
        "a_b.py",
        "a中文b.py",
        "#a.py",
        "中文名.py",
        "src/keep.py",
        "src/nested/keep.py",
        "a/b.py",
        "a/nested/b.py",
    )
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    # --no-index使本轮只比较忽略匹配；固定参数、无用户配置、无Shell执行。
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.ignorecase=false",
            "check-ignore",
            "--no-index",
            "-z",
            "--stdin",
        ],
        cwd=tmp_path,
        env=environment,
        input=("\x00".join(paths) + "\x00").encode(),
        capture_output=True,
        check=False,
    )
    assert result.returncode in {0, 1}
    native = (
        set(result.stdout.decode().rstrip("\x00").split("\x00"))
        if result.stdout
        else set()
    )
    rules = parse_code_ignore(pattern)
    for path in paths:
        parts = PurePosixPath(path).parts
        # 复现扫描器的父目录剪枝，不能靠子文件否定恢复已忽略父目录。
        matched = any(
            ignored_by_rules(parts[:index], is_directory=True, rules=rules)
            for index in range(1, len(parts))
        )
        matched = matched or ignored_by_rules(parts, is_directory=False, rules=rules)
        assert matched is (path in native), (pattern, path, native)
