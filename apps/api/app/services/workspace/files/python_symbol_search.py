"""当前授权源码的精确符号检索与有界引用；不从旧清单重新读取。"""

from dataclasses import asdict, dataclass
import json
import sys
from typing import Literal
import unicodedata

from app.services.workspace.files.code_inventory import (
    CodeFile,
    IncompleteReason,
    _scan_code_inventory,
)
from app.services.workspace.files.python_symbols import (
    PythonSymbol,
    PythonSymbolsError,
    _parse_python_symbols,
)


# 查询、命中、片段与完整序列化各自限量，调用方不能覆盖这些预算。
MAX_SYMBOL_QUERY_BYTES = 1024
MAX_SYMBOL_MATCHES = 20
MAX_DEFINITION_LINES = 20
MAX_DEFINITION_CHARS = 1000
MAX_DEFINITION_BYTES = 2048
MAX_SEARCH_RESULT_BYTES = 64 * 1024

SearchIncompleteReason = IncompleteReason | Literal["match_budget"]
SnippetIncompleteReason = Literal[
    "decorator_budget", "line_budget", "character_budget", "byte_budget"
]
MatchReason = Literal["name", "qualified_name"]


class PythonSymbolSearchError(PythonSymbolsError):
    """共用安全业务错误边界，不包含原始查询、正文或宿主路径。"""


@dataclass(frozen=True)
class SymbolQuery:
    original: str
    normalized: str


@dataclass(frozen=True)
class DefinitionSnippet:
    text: str
    start_line: int
    end_line: int
    # 列坐标按Unicode码点，最后一列包含在片段内，不使用AST的UTF-8列偏移。
    start_column: int
    end_column: int
    truncated: bool
    incomplete_reasons: tuple[SnippetIncompleteReason, ...]


@dataclass(frozen=True)
class PythonSymbolMatch:
    symbol: PythonSymbol
    matched_by: MatchReason
    snippet: DefinitionSnippet


@dataclass(frozen=True)
class PythonSymbolSearchResult:
    workspace_id: str
    task_id: str
    query: str
    normalized_query: str
    files: tuple[CodeFile, ...]
    matches: tuple[PythonSymbolMatch, ...]
    scanned_directories: int
    inspected_files: int
    searched_files: int
    examined_symbols: int
    matched_symbols: int
    excluded_counts: dict[str, int]
    truncated: bool
    incomplete_reasons: tuple[SearchIncompleteReason, ...]
    parser: str
    source: str = "authorized_python_symbol_search"
    policy: str = "gitignore_subset_v1"
    content_trust: str = "untrusted_project_content"


def validate_symbol_query(value: object) -> SymbolQuery:
    """只接收名称或词法限定名称；先验证输入，避免非法查询触发文件I/O。"""

    def invalid() -> PythonSymbolSearchError:
        return PythonSymbolSearchError(
            "invalid_python_symbol_query", "请输入有效且有界的Python符号名称"
        )

    if not isinstance(value, str) or not value or len(value) > MAX_SYMBOL_QUERY_BYTES:
        raise invalid()
    try:
        if len(value.encode("utf-8")) > MAX_SYMBOL_QUERY_BYTES:
            raise invalid()
    except UnicodeError:
        raise invalid() from None
    # Python在解析标识符时做NFKC；查询使用相同规则并公开规范化后的名称。
    normalized = unicodedata.normalize("NFKC", value)
    if len(normalized.encode("utf-8")) > MAX_SYMBOL_QUERY_BYTES:
        raise invalid()
    for candidate in (value, normalized):
        if any(not part.isidentifier() for part in candidate.split(".")):
            raise invalid()
    return SymbolQuery(original=value, normalized=normalized)


def _definition_snippet(symbol: PythonSymbol, lines: list[str]) -> DefinitionSnippet:
    reasons: list[SnippetIncompleteReason] = []
    start = symbol.start_line
    decorators = "\n".join(lines[start - 1 : symbol.definition_line - 1])
    # 长装饰器不应耗尽片段后隐藏定义行；从定义行开始并明确省略前部。
    if (
        symbol.definition_line - start >= MAX_DEFINITION_LINES
        or len(decorators) + int(bool(decorators)) >= MAX_DEFINITION_CHARS
        or len(decorators.encode("utf-8")) + int(bool(decorators))
        >= MAX_DEFINITION_BYTES
    ):
        start = symbol.definition_line
        reasons.append("decorator_budget")
    end = min(symbol.end_line, start + MAX_DEFINITION_LINES - 1)
    if end < symbol.end_line:
        reasons.append("line_budget")
    text = "\n".join(lines[start - 1 : end])
    if len(text) > MAX_DEFINITION_CHARS:
        text = text[:MAX_DEFINITION_CHARS]
        reasons.append("character_budget")
    if len(text.encode("utf-8")) > MAX_DEFINITION_BYTES:
        # 输入已经是严格UTF-8，只去掉前缀末端未完整的码点，不丢弃中间正文。
        text = text.encode("utf-8")[:MAX_DEFINITION_BYTES].decode(
            "utf-8", errors="ignore"
        )
        reasons.append("byte_budget")
    # 裁剪恰好落在换行上时，不把尚未展示的下一行计为片段末行。
    text = text.rstrip("\n")
    return DefinitionSnippet(
        text=text,
        start_line=start,
        end_line=start + text.count("\n"),
        start_column=1,
        end_column=len(text.rsplit("\n", 1)[-1]),
        truncated=bool(reasons),
        incomplete_reasons=tuple(reasons),
    )


def search_python_symbols(
    *, user_id: int, workspace_id: str, task_id: str, query: str
) -> PythonSymbolSearchResult:
    validated = validate_symbol_query(query)
    files: list[CodeFile] = []
    matches: list[PythonSymbolMatch] = []
    examined = matched = 0
    matched_by: MatchReason = (
        "qualified_name" if "." in validated.normalized else "name"
    )

    def consume_file(file: CodeFile, content: str) -> None:
        nonlocal examined, matched
        if file.file_type != "source" or file.language != "python":
            return
        # 只按Python物理行规范化，不能用splitlines把字符串内Unicode分隔符当换行。
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")

        def consume_symbol(symbol: PythonSymbol) -> None:
            nonlocal examined, matched
            examined += 1
            candidate = symbol.name if matched_by == "name" else symbol.qualified_name
            if candidate != validated.normalized:
                return
            matched += 1
            if len(matches) < MAX_SYMBOL_MATCHES:
                matches.append(
                    PythonSymbolMatch(
                        symbol=symbol,
                        matched_by=matched_by,
                        snippet=_definition_snippet(symbol, lines),
                    )
                )

        # 每个定义在清单截断之前参与匹配；输出满后仍解析，未知状态不能当成功。
        _parse_python_symbols(file, content, consume=consume_symbol)
        files.append(file)

    # 共用本轮授权、过滤和同次正文；Session在I/O前结束，解析与检索不持有事务。
    inventory = _scan_code_inventory(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        consume=consume_file,
    )
    reasons: list[SearchIncompleteReason] = list(inventory.incomplete_reasons)
    if matched > MAX_SYMBOL_MATCHES:
        reasons.append("match_budget")
    result = PythonSymbolSearchResult(
        workspace_id=workspace_id,
        task_id=task_id,
        query=validated.original,
        normalized_query=validated.normalized,
        files=tuple(sorted(files, key=lambda file: file.relative_path)),
        matches=tuple(
            sorted(
                matches,
                key=lambda item: (
                    item.symbol.relative_path,
                    item.symbol.definition_line,
                ),
            )
        ),
        scanned_directories=inventory.scanned_directories,
        inspected_files=inventory.inspected_files,
        searched_files=len(files),
        examined_symbols=examined,
        matched_symbols=matched,
        excluded_counts=inventory.excluded_counts,
        truncated=bool(reasons),
        incomplete_reasons=tuple(reasons),
        parser=f"python_ast_{sys.version_info.major}_{sys.version_info.minor}",
    )
    # JSON转义也占预算，超限整次拒绝，不切断JSON或删掉来源/覆盖字段。
    payload = json.dumps(
        asdict(result), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(payload.encode("utf-8")) > MAX_SEARCH_RESULT_BYTES:
        raise PythonSymbolSearchError(
            "python_symbol_search_result_too_large", "符号检索结果超过输出预算"
        )
    return result
