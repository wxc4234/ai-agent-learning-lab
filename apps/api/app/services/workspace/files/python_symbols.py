"""授权 Python 候选的有界静态符号；不导入、编译执行或返回正文。"""

import ast
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from io import StringIO
import json
import sys
import tokenize
from typing import Literal

from app.services.workspace.files.code_inventory import (
    CodeFile,
    IncompleteReason,
    _scan_code_inventory,
)


# 解析前限制输入与词法复杂度，解析后另限制AST；两者不能互相替代。
MAX_PYTHON_BYTES = 64 * 1024
MAX_PYTHON_TOKENS = 4096
MAX_LOGICAL_LINE_TOKENS = 256
MAX_BRACKET_DEPTH = 32
MAX_INDENT_DEPTH = 32
MAX_AST_NODES = 8192
MAX_AST_DEPTH = 64
MAX_PYTHON_SYMBOLS = 200
MAX_SYMBOL_NAME_BYTES = 1024
MAX_SYMBOL_RESULT_BYTES = 64 * 1024

SymbolKind = Literal["function", "async_function", "class"]
SymbolIncompleteReason = IncompleteReason | Literal["symbol_budget"]


class PythonSymbolsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _budget_error() -> PythonSymbolsError:
    return PythonSymbolsError(
        "python_parse_budget_exceeded", "Python源码超出解析预算，未返回符号清单"
    )


def _syntax_error() -> PythonSymbolsError:
    # SyntaxError/TokenError可能包含源码行或文件名，不能向HTTP反射它们。
    return PythonSymbolsError(
        "python_syntax_invalid", "Python源码无法按当前语法解析，未返回符号清单"
    )


@dataclass(frozen=True)
class PythonSymbol:
    relative_path: str
    name: str
    # 词法嵌套路径，不是运行时属性或引用解析结果；重名定义保留各自位置。
    qualified_name: str
    kind: SymbolKind
    start_line: int
    definition_line: int
    end_line: int
    # 来自同次授权读取的整文件摘要，不能用符号片段摘要替代。
    sha256: str


@dataclass(frozen=True)
class ParsedPython:
    symbols: tuple[PythonSymbol, ...]
    truncated: bool


@dataclass(frozen=True)
class PythonSymbolInventory:
    workspace_id: str
    task_id: str
    files: tuple[CodeFile, ...]
    symbols: tuple[PythonSymbol, ...]
    scanned_directories: int
    inspected_files: int
    parsed_files: int
    excluded_counts: dict[str, int]
    truncated: bool
    incomplete_reasons: tuple[SymbolIncompleteReason, ...]
    parser: str
    source: str = "authorized_python_symbols"
    policy: str = "gitignore_subset_v1"


def _check_lexical_budget(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_PYTHON_BYTES:
        raise _budget_error()
    count = logical_count = brackets = indentation = 0
    try:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        for token in tokenize.generate_tokens(StringIO(normalized).readline):
            count += 1
            logical_count += 1
            if count > MAX_PYTHON_TOKENS or logical_count > MAX_LOGICAL_LINE_TOKENS:
                raise _budget_error()
            if token.type == tokenize.NEWLINE or (
                token.type == tokenize.NL and brackets == 0
            ):
                logical_count = 0
            elif token.type == tokenize.INDENT:
                indentation += 1
            elif token.type == tokenize.DEDENT:
                indentation -= 1
            elif token.type == tokenize.OP:
                if token.string in {"(", "[", "{"}:
                    brackets += 1
                elif token.string in {")", "]", "}"}:
                    brackets -= 1
            if brackets > MAX_BRACKET_DEPTH or indentation > MAX_INDENT_DEPTH:
                raise _budget_error()
            if brackets < 0 or token.type == tokenize.ERRORTOKEN:
                raise _syntax_error()
    except PythonSymbolsError:
        raise
    except (tokenize.TokenError, SyntaxError, ValueError):
        raise _syntax_error() from None
    except (RecursionError, MemoryError):
        raise _budget_error() from None


def parse_python_symbols(file: CodeFile, content: str) -> ParsedPython:
    """纯解析：正文由内部扫描提供；本函数本身不授予文件访问权限。"""

    return _parse_python_symbols(file, content, consume=None)


def _parse_python_symbols(
    file: CodeFile,
    content: str,
    *,
    consume: Callable[[PythonSymbol], None] | None,
) -> ParsedPython:
    """内部消费者在清单截断前检查每个定义；仍须完成整树校验。"""

    _check_lexical_budget(content)
    try:
        # filename固定，避免解释器异常携带宿主路径；不调用exec/eval或源码导入。
        tree = ast.parse(content, filename="<authorized-python>", mode="exec")
    except (SyntaxError, ValueError):
        raise _syntax_error() from None
    except (RecursionError, MemoryError):
        raise _budget_error() from None

    # AST行号按CRLF/CR/LF计，末尾换行不增加最后一个真实源码行。
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    line_count = len(normalized.split("\n")) - int(normalized.endswith("\n"))
    symbols: list[PythonSymbol] = []
    truncated = False
    visited = 0
    # 显式迭代器栈避免递归Visitor；不一次性展开所有子节点。
    stack: list[tuple[Iterator[ast.AST], tuple[str, ...], int]] = [
        (iter((tree,)), (), 0)
    ]
    while stack:
        nodes, scope, depth = stack[-1]
        try:
            node = next(nodes)
        except StopIteration:
            stack.pop()
            continue
        visited += 1
        if visited > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise _budget_error()
        child_scope = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            child_scope = (*scope, node.name)
            qualified = ".".join(child_scope)
            if len(qualified.encode("utf-8")) > MAX_SYMBOL_NAME_BYTES:
                raise _budget_error()
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            end = node.end_lineno
            if end is None or not (1 <= start <= node.lineno <= end <= line_count):
                raise PythonSymbolsError(
                    "python_symbol_invalid", "Python符号位置未知，未返回符号清单"
                )
            kind: SymbolKind = (
                "class"
                if isinstance(node, ast.ClassDef)
                else "async_function"
                if isinstance(node, ast.AsyncFunctionDef)
                else "function"
            )
            symbol = PythonSymbol(
                relative_path=file.relative_path,
                name=node.name,
                qualified_name=qualified,
                kind=kind,
                start_line=start,
                definition_line=node.lineno,
                end_line=end,
                sha256=file.sha256,
            )
            if consume is not None:
                consume(symbol)
            if len(symbols) == MAX_PYTHON_SYMBOLS:
                truncated = True
            else:
                symbols.append(symbol)
        # 即使符号输出已满也校验剩余树，不能隐藏解析错误/复杂度失败。
        stack.append((ast.iter_child_nodes(node), child_scope, depth + 1))
    return ParsedPython(symbols=tuple(symbols), truncated=truncated)


def scan_python_symbols(
    *, user_id: int, workspace_id: str, task_id: str
) -> PythonSymbolInventory:
    files: list[CodeFile] = []
    symbols: list[PythonSymbol] = []
    symbol_truncated = False

    def consume(file: CodeFile, content: str) -> None:
        nonlocal symbol_truncated
        if file.file_type != "source" or file.language != "python":
            return
        # 共用本次授权/策略过滤后的正文，不用旧清单触发二次读取。
        parsed = parse_python_symbols(file, content)
        files.append(file)
        remaining = MAX_PYTHON_SYMBOLS - len(symbols)
        if parsed.truncated or len(parsed.symbols) > remaining:
            symbol_truncated = True
        symbols.extend(parsed.symbols[:remaining])

    # 下层每次I/O重新授权并先关闭Session；解析时也没有持有数据库事务。
    inventory = _scan_code_inventory(
        user_id=user_id, workspace_id=workspace_id, task_id=task_id, consume=consume
    )
    reasons: list[SymbolIncompleteReason] = list(inventory.incomplete_reasons)
    if symbol_truncated:
        reasons.append("symbol_budget")
    result = PythonSymbolInventory(
        workspace_id=inventory.workspace_id,
        task_id=inventory.task_id,
        files=tuple(sorted(files, key=lambda item: item.relative_path)),
        symbols=tuple(
            sorted(symbols, key=lambda item: (item.relative_path, item.definition_line))
        ),
        scanned_directories=inventory.scanned_directories,
        inspected_files=inventory.inspected_files,
        parsed_files=len(files),
        excluded_counts=inventory.excluded_counts,
        truncated=bool(reasons),
        incomplete_reasons=tuple(reasons),
        parser=f"python_ast_{sys.version_info.major}_{sys.version_info.minor}",
    )
    # 与JSONResponse相同编码，完整JSON超限整次拒绝，不能切断或丢覆盖标记。
    payload = json.dumps(
        asdict(result), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(payload.encode("utf-8")) > MAX_SYMBOL_RESULT_BYTES:
        raise PythonSymbolsError(
            "python_symbols_result_too_large", "Python符号结果超过输出预算"
        )
    return result
