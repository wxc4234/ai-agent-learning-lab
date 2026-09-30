"""静态符号语义、解析预算和真实受限文件扫描；不执行被扫描源码。"""

import ast
from dataclasses import asdict
from hashlib import sha256
import json
import tokenize
from io import StringIO

import pytest

from app.services.workspace.files import code_inventory, python_symbols as service
from app.services.workspace.files.code_inventory import CodeFile
from app.services.workspace.files.code_ignore import CodeIgnoreError
from app.services.workspace.files.workspace_file import WorkspaceFileError
from tests.workspace.files.test_code_inventory import root


__all__ = ["root"]


def metadata(content, path="module.py"):
    data = content.encode("utf-8")
    return CodeFile(path, "source", "python", len(data), sha256(data).hexdigest())


def parse(content):
    return service.parse_python_symbols(metadata(content), content)


def scan():
    return service.scan_python_symbols(
        user_id=1, workspace_id="a" * 32, task_id="b" * 32
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_lexical_scope_decorators_duplicates_and_exact_line_ranges(newline):
    content = newline.join(
        [
            "@decorate(",
            '    "SOURCE_PRIVATE"',
            ")",
            "class 外层:",
            "    @staticmethod",
            "    async def run(self):",
            "        def inner():",
            "            return 1",
            "        return inner()",
            "",
            "if condition:",
            "    def choose():",
            "        pass",
            "def choose():",
            "    pass",
        ]
    )
    result = parse(content)
    assert not result.truncated
    assert [
        (
            item.qualified_name,
            item.kind,
            item.start_line,
            item.definition_line,
            item.end_line,
        )
        for item in result.symbols
    ] == [
        ("外层", "class", 1, 4, 9),
        ("外层.run", "async_function", 5, 6, 9),
        ("外层.run.inner", "function", 7, 7, 8),
        ("choose", "function", 12, 12, 13),
        ("choose", "function", 14, 14, 15),
    ]
    assert all(item.sha256 == metadata(content).sha256 for item in result.symbols)
    assert all(item.relative_path == "module.py" for item in result.symbols)
    assert "SOURCE_PRIVATE" not in repr(asdict(result))


@pytest.mark.parametrize(
    "content", ["", "# comment\n", "return 42\n", "x = lambda: 1\n"]
)
def test_parse_is_declarations_not_executability_or_all_bindings(content):
    # ast.parse不做运行作用域检查；lambda/赋值不是本课定义的符号。
    assert parse(content).symbols == ()


def test_imports_decorators_and_top_level_calls_are_never_executed(tmp_path):
    marker = tmp_path / "must-not-exist"
    content = (
        "import module_that_does_not_exist\n"
        f"open({str(marker)!r}, 'w').write('SIDE_EFFECT')\n"
        "raise RuntimeError('SIDE_EFFECT')\n"
        "@explode()\n"
        "def safe():\n"
        "    return explode()\n"
    )
    assert [item.name for item in parse(content).symbols] == ["safe"]
    assert not marker.exists()


@pytest.mark.parametrize(
    "content",
    [
        "def SOURCE_PRIVATE(:\n",
        "def f():\n",
        "x = (",
        "x = '\n",
        "x = )\n",
        "x = @\n",
        "x\x00y",
        "if True:\n  pass\n pass\n",
    ],
)
def test_syntax_failures_never_reflect_source(content):
    with pytest.raises(service.PythonSymbolsError) as caught:
        parse(content)
    assert caught.value.code == "python_syntax_invalid"
    assert "SOURCE_PRIVATE" not in str(caught.value) and content not in str(
        caught.value
    )


@pytest.mark.parametrize(
    "constant,content,exact",
    [
        ("MAX_PYTHON_BYTES", "# 中文\n", len("# 中文\n".encode())),
        ("MAX_BRACKET_DEPTH", "x = ([1])\n", 2),
        ("MAX_INDENT_DEPTH", "def f():\n    def g():\n        pass\n", 2),
        ("MAX_AST_DEPTH", "def f(): return g(1)\n", 5),
        ("MAX_SYMBOL_NAME_BYTES", "def 汉(): pass\n", 3),
    ],
)
def test_exact_parse_budgets_then_one_over(monkeypatch, constant, content, exact):
    monkeypatch.setattr(service, constant, exact)
    parse(content)
    monkeypatch.setattr(service, constant, exact - 1)
    with pytest.raises(service.PythonSymbolsError) as caught:
        parse(content)
    assert caught.value.code == "python_parse_budget_exceeded"


@pytest.mark.parametrize(
    "constant", ["MAX_PYTHON_TOKENS", "MAX_LOGICAL_LINE_TOKENS", "MAX_AST_NODES"]
)
def test_token_and_node_budget_boundaries(monkeypatch, constant):
    content = "def f(): return 1\n"
    tokens = list(tokenize.generate_tokens(StringIO(content).readline))
    exact = (
        len(tokens)
        if constant == "MAX_PYTHON_TOKENS"
        else next(
            i + 1 for i, token in enumerate(tokens) if token.type == tokenize.NEWLINE
        )
        if constant == "MAX_LOGICAL_LINE_TOKENS"
        else len(list(ast.walk(ast.parse(content))))
    )
    monkeypatch.setattr(service, constant, exact)
    parse(content)
    monkeypatch.setattr(service, constant, exact - 1)
    with pytest.raises(service.PythonSymbolsError) as caught:
        parse(content)
    assert caught.value.code == "python_parse_budget_exceeded"


def test_lexical_limit_precedes_ast_parser(monkeypatch):
    monkeypatch.setattr(service, "MAX_BRACKET_DEPTH", 1)
    monkeypatch.setattr(
        service.ast,
        "parse",
        lambda *args, **kwargs: pytest.fail("AST before preflight"),
    )
    with pytest.raises(service.PythonSymbolsError) as caught:
        parse("x = ((1))\n")
    assert caught.value.code == "python_parse_budget_exceeded"


@pytest.mark.parametrize("error", [RecursionError("PRIVATE"), MemoryError("PRIVATE")])
def test_parser_resource_failure_has_fixed_message(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(service.ast, "parse", fail)
    with pytest.raises(service.PythonSymbolsError) as caught:
        parse("def f(): pass\n")
    assert caught.value.code == "python_parse_budget_exceeded" and "PRIVATE" not in str(
        caught.value
    )


@pytest.mark.parametrize("end", [None, 0, 1000])
def test_unknown_ast_location_rejects_instead_of_inventing_range(monkeypatch, end):
    tree = ast.parse("def f(): pass\n")
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    node.end_lineno = end
    monkeypatch.setattr(service.ast, "parse", lambda *args, **kwargs: tree)
    with pytest.raises(service.PythonSymbolsError) as caught:
        parse("def f(): pass\n")
    assert caught.value.code == "python_symbol_invalid"


@pytest.mark.parametrize("count", [200, 201])
def test_symbol_cap_needs_observed_extra_definition(count):
    result = parse("".join(f"def f{i}(): pass\n" for i in range(count)))
    assert len(result.symbols) == 200 and result.truncated is (count == 201)


def test_ast_validation_continues_after_symbol_output_is_full(monkeypatch):
    monkeypatch.setattr(service, "MAX_PYTHON_SYMBOLS", 1)
    monkeypatch.setattr(service, "MAX_AST_DEPTH", 3)
    with pytest.raises(service.PythonSymbolsError) as caught:
        parse("def f(): pass\ndef g(): return call(1)\n")
    assert caught.value.code == "python_parse_budget_exceeded"


def write(root, path, content):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8", newline="")
    return file


def test_scan_filters_before_parser_and_reads_source_once(root, monkeypatch):
    write(root, ".gitignore", "ignored.py\n")
    write(root, "ignored.py", "INVALID[\n")
    write(root, "node_modules/hidden.py", "INVALID[\n")
    write(root, "secrets.py", "INVALID[\n")
    write(root, ".hidden.py", "INVALID[\n")
    write(root, "sensitive.py", "password = 'PRIVATE'\n")
    write(root, "client.ts", "INVALID[\n")
    (root / "binary.py").write_bytes(b"\xff")
    write(root, "safe.py", "def safe():\r\n    return 1\r\n")
    write(root, "stub.pyi", "class Contract: ...\n")
    (root / "link.py").symlink_to(root / "ignored.py")
    before = {
        path: path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    parsed = []
    reads = []
    original_parse = service.parse_python_symbols
    original_read = code_inventory.read_task_text_file

    def capture_parse(file, content):
        parsed.append(file.relative_path)
        return original_parse(file, content)

    def capture_read(**kwargs):
        reads.append(kwargs["relative_path"])
        return original_read(**kwargs)

    monkeypatch.setattr(service, "parse_python_symbols", capture_parse)
    monkeypatch.setattr(code_inventory, "read_task_text_file", capture_read)
    result = scan()
    assert sorted(parsed) == ["safe.py", "stub.pyi"]
    assert reads.count("safe.py") == reads.count("stub.pyi") == 1
    assert reads.count(".gitignore") == 2
    assert result.parsed_files == 2 and result.inspected_files == 5
    assert [item.relative_path for item in result.files] == ["safe.py", "stub.pyi"]
    assert [item.name for item in result.symbols] == ["safe", "Contract"]
    assert (
        result.source == "authorized_python_symbols"
        and result.parser == "python_ast_3_12"
    )
    assert not result.truncated and result.incomplete_reasons == ()
    assert all(path.read_bytes() == content for path, content in before.items())


def test_same_read_content_and_hash_even_if_file_is_edited_after_read(
    root, monkeypatch
):
    content = "def before(): pass\n"
    file = write(root, "safe.py", content)
    original = code_inventory.read_task_text_file

    def read(**kwargs):
        result = original(**kwargs)
        if kwargs["relative_path"] == "safe.py":
            file.write_text("def after(): pass\n")
        return result

    monkeypatch.setattr(code_inventory, "read_task_text_file", read)
    result = scan()
    assert result.symbols[0].name == "before"
    assert (
        result.symbols[0].sha256 == result.files[0].sha256 == metadata(content).sha256
    )


@pytest.mark.parametrize("change", ["rule", "syntax", "read"])
def test_failure_discards_any_prior_symbols(root, monkeypatch, change):
    write(root, "first.py", "def first(): pass\n")
    original = code_inventory.read_task_text_file

    def read(**kwargs):
        result = original(**kwargs)
        if kwargs["relative_path"] == "first.py":
            if change == "rule":
                write(root, ".gitignore", "first.py\n")
        elif kwargs["relative_path"] == "second.py" and change == "read":
            raise WorkspaceFileError("file_changed", "读取变化")
        return result

    if change != "rule":
        write(
            root,
            "second.py",
            "def second(:\n" if change == "syntax" else "def second(): pass\n",
        )
    monkeypatch.setattr(code_inventory, "read_task_text_file", read)
    with pytest.raises(
        (
            code_inventory.CodeInventoryError,
            service.PythonSymbolsError,
            WorkspaceFileError,
        )
    ):
        scan()


def test_unknown_ignore_is_not_an_empty_symbol_success(root):
    write(root, ".gitignore", "[\n")
    write(root, "safe.py", "def safe(): pass\n")
    with pytest.raises(CodeIgnoreError):
        scan()


def test_global_symbol_cap_and_inherited_coverage(root, monkeypatch):
    monkeypatch.setattr(service, "MAX_PYTHON_SYMBOLS", 3)
    monkeypatch.setattr(code_inventory, "MAX_CODE_DIRECTORIES", 1)
    write(root, "a.py", "def a(): pass\ndef b(): pass\n")
    write(root, "b.py", "def c(): pass\ndef d(): pass\n")
    write(root, "child/c.py", "def hidden(): pass\n")
    result = scan()
    assert result.parsed_files == 2 and len(result.symbols) == 3
    assert result.truncated and set(result.incomplete_reasons) == {
        "directory_budget",
        "symbol_budget",
    }


def test_shared_file_budget_still_includes_non_python_candidates(root, monkeypatch):
    monkeypatch.setattr(code_inventory, "MAX_CODE_READS", 1)
    write(root, "client.ts", "const value = 1;\n")
    write(root, "safe.py", "def safe(): pass\n")
    result = scan()
    assert result.inspected_files == 1 and result.parsed_files <= 1
    assert result.truncated and result.incomplete_reasons == ("file_budget",)


@pytest.mark.parametrize("content", ["# no definitions\n", ""])
def test_empty_symbols_retain_actually_parsed_file(root, content):
    write(root, "safe.py", content)
    result = scan()
    assert result.symbols == () and result.parsed_files == 1
    assert len(result.files) == 1 and not result.truncated


def test_complete_json_utf8_budget_with_exact_boundary(root, monkeypatch):
    write(root, "中文.py", "def 汉(): pass\n")
    result = scan()
    payload = json.dumps(
        asdict(result), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    size = len(payload.encode("utf-8"))
    assert size > len(payload)
    monkeypatch.setattr(service, "MAX_SYMBOL_RESULT_BYTES", size)
    assert scan() == result
    monkeypatch.setattr(service, "MAX_SYMBOL_RESULT_BYTES", size - 1)
    with pytest.raises(service.PythonSymbolsError) as caught:
        scan()
    assert caught.value.code == "python_symbols_result_too_large"
