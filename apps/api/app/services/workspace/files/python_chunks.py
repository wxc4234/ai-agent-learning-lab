"""当前授权Python定义的有界分块；嵌套正文只归属最内层定义。"""

from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import sys
from typing import Literal

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


# 分块预算与覆盖预算独立；调用方不能扩大这些服务端固定限制。
MAX_CODE_CHUNKS = 20
MAX_CHUNK_LINES = 40
MAX_CHUNK_CHARS = 2000
MAX_CHUNK_BYTES = 4096
MAX_CHUNK_RESULT_BYTES = 128 * 1024
CHUNK_STRATEGY = "python_innermost_definitions_v1"

ChunkIncompleteReason = IncompleteReason | Literal["chunk_budget"]
ChunkSplitReason = Literal[
    "nested_definition", "line_budget", "character_budget", "byte_budget"
]


class PythonChunksError(PythonSymbolsError):
    """仅提供静态业务错误；不把正文、查询或宿主路径放进错误。"""


@dataclass(frozen=True)
class PythonCodeChunk:
    chunk_id: str
    symbol: PythonSymbol
    text: str
    # 分块使用1起始Unicode码点坐标，起点包含、终点不包含。
    # 末尾LF的终点位于下一行第1列，不能沿用检索片段的包含式末列。
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    line_count: int
    # text_sha256对应LF归一化后的分块；symbol.sha256仍是整文件原始摘要。
    text_sha256: str
    part_index: int
    part_count: int
    split_reasons: tuple[ChunkSplitReason, ...]


@dataclass(frozen=True)
class PythonCodeChunks:
    workspace_id: str
    task_id: str
    files: tuple[CodeFile, ...]
    chunks: tuple[PythonCodeChunk, ...]
    scanned_directories: int
    inspected_files: int
    parsed_files: int
    examined_symbols: int
    generated_chunks: int
    definition_lines: int
    excluded_module_lines: int
    excluded_counts: dict[str, int]
    truncated: bool
    incomplete_reasons: tuple[ChunkIncompleteReason, ...]
    parser: str
    source: str = "authorized_python_code_chunks"
    policy: str = "gitignore_subset_v1"
    strategy: str = CHUNK_STRATEGY
    content_trust: str = "untrusted_project_content"


def _symbol_regions(
    symbols: list[PythonSymbol],
) -> Iterator[tuple[PythonSymbol, int, int]]:
    """把嵌套区间切成互不重叠的连续行区间；模块正文不在支持范围。"""

    stack: list[PythonSymbol] = []
    cursor = 1
    for symbol in sorted(symbols, key=lambda item: (item.start_line, -item.end_line)):
        while stack and stack[-1].end_line < symbol.start_line:
            ended = stack.pop()
            if cursor <= ended.end_line:
                yield ended, cursor, ended.end_line
            cursor = ended.end_line + 1
        if stack:
            if symbol.end_line > stack[-1].end_line or symbol.start_line < cursor:
                raise PythonChunksError(
                    "python_chunk_invalid", "Python定义区间无法确认，未返回代码分块"
                )
            if cursor < symbol.start_line:
                yield stack[-1], cursor, symbol.start_line - 1
        stack.append(symbol)
        cursor = symbol.start_line
    while stack:
        ended = stack.pop()
        if cursor <= ended.end_line:
            yield ended, cursor, ended.end_line
        cursor = ended.end_line + 1


def _split_region(
    text: str, start_line: int
) -> Iterator[tuple[str, int, int, int, int, tuple[ChunkSplitReason, ...]]]:
    """优先在完整物理行后拆分；超长单行按完整码点拆分且继续处理余下正文。"""

    cursor = 0
    line, column = start_line, 1
    while cursor < len(text):
        end = cursor
        bytes_used = newlines = 0
        last_line_end = cursor
        reasons: list[ChunkSplitReason] = []
        while end < len(text):
            char_bytes = len(text[end].encode("utf-8"))
            if newlines == MAX_CHUNK_LINES:
                reasons.append("line_budget")
            if end - cursor == MAX_CHUNK_CHARS:
                reasons.append("character_budget")
            if bytes_used + char_bytes > MAX_CHUNK_BYTES:
                reasons.append("byte_budget")
            if reasons:
                break
            bytes_used += char_bytes
            if text[end] == "\n":
                newlines += 1
                last_line_end = end + 1
            end += 1
        if end == cursor:
            raise PythonChunksError(
                "python_chunk_budget_exceeded", "代码分块预算无法容纳完整字符"
            )
        if reasons and last_line_end > cursor:
            end = last_line_end
        piece = text[cursor:end]
        end_line = line + piece.count("\n")
        end_column = (
            len(piece.rsplit("\n", 1)[-1]) + 1 if "\n" in piece else column + len(piece)
        )
        yield piece, line, column, end_line, end_column, tuple(reasons)
        cursor = end
        line, column = end_line, end_column


def _chunk(
    symbol: PythonSymbol,
    text: str,
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
    part_index: int,
) -> PythonCodeChunk:
    digest = sha256(text.encode("utf-8")).hexdigest()
    # ID绑定策略、文件版本、词法定义和实际范围；不能用同名符号或正文摘要单独去重。
    identity = json.dumps(
        [
            CHUNK_STRATEGY,
            symbol.relative_path,
            symbol.sha256,
            symbol.qualified_name,
            symbol.kind,
            symbol.definition_line,
            start_line,
            start_column,
            end_line,
            end_column,
            digest,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return PythonCodeChunk(
        chunk_id=sha256(identity.encode("utf-8")).hexdigest(),
        symbol=symbol,
        text=text,
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
        line_count=text.count("\n") + int(not text.endswith("\n")),
        text_sha256=digest,
        part_index=part_index,
        part_count=0,
        split_reasons=(),
    )


def build_python_code_chunks(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    expected_bound_root: str | None = None,
    expected_binding_revision: int | None = None,
) -> PythonCodeChunks:
    """只从本轮授权正文产生分块；输出满后仍解析、计数和完成最终复核。"""

    files: list[CodeFile] = []
    chunks: list[PythonCodeChunk] = []
    examined = generated = definition_lines = module_lines = 0

    def consume_file(file: CodeFile, content: str) -> None:
        nonlocal examined, generated, definition_lines, module_lines
        if file.file_type != "source" or file.language != "python":
            return
        symbols: list[PythonSymbol] = []
        # 在清单200项截断前收集定义；整树解析预算仍限制内部集合，不静默遗漏定义。
        _parse_python_symbols(file, content, consume=symbols.append)
        examined += len(symbols)
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        # 只按Python物理行拆分，字符串中的Unicode分隔符不应改变来源行号。
        lines = normalized.split("\n")
        physical = len(lines) - int(normalized.endswith("\n")) if normalized else 0
        # 保留原有行末LF，分块按顺序拼接可还原支持区间的归一化正文。
        lines = [line + "\n" for line in lines[:-1]] + lines[-1:]
        counts: Counter[PythonSymbol] = Counter()
        splits: dict[PythonSymbol, list[ChunkSplitReason]] = {}
        selected: list[PythonCodeChunk] = []
        covered = 0
        for symbol, start, end in _symbol_regions(symbols):
            covered += end - start + 1
            reasons = splits.setdefault(symbol, [])
            if (
                start != symbol.start_line or end != symbol.end_line
            ) and "nested_definition" not in reasons:
                reasons.append("nested_definition")
            for piece, first, col, last, last_col, limits in _split_region(
                "".join(lines[start - 1 : end]), start
            ):
                counts[symbol] += 1
                generated += 1
                for limit in limits:
                    if limit not in reasons:
                        reasons.append(limit)
                if len(chunks) + len(selected) < MAX_CODE_CHUNKS:
                    selected.append(
                        _chunk(
                            symbol, piece, first, col, last, last_col, counts[symbol]
                        )
                    )
        # 数量与拆分原因描述该定义在本次解析中的全部分片，而非仅返回的前缀。
        chunks.extend(
            replace(
                chunk,
                part_count=counts[chunk.symbol],
                split_reasons=tuple(splits[chunk.symbol]),
            )
            for chunk in selected
        )
        definition_lines += covered
        module_lines += physical - covered
        files.append(file)

    # 共用扫描授权、忽略和敏感过滤；Session在I/O前关闭，分块不持有事务或写索引。
    inventory = _scan_code_inventory(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        consume=consume_file,
        expected_bound_root=expected_bound_root,
        expected_binding_revision=expected_binding_revision,
    )
    reasons: list[ChunkIncompleteReason] = list(inventory.incomplete_reasons)
    if generated > MAX_CODE_CHUNKS:
        reasons.append("chunk_budget")
    result = PythonCodeChunks(
        workspace_id=workspace_id,
        task_id=task_id,
        files=tuple(sorted(files, key=lambda file: file.relative_path)),
        chunks=tuple(
            sorted(
                chunks,
                key=lambda item: (
                    item.symbol.relative_path,
                    item.start_line,
                    item.start_column,
                ),
            )
        ),
        scanned_directories=inventory.scanned_directories,
        inspected_files=inventory.inspected_files,
        parsed_files=len(files),
        examined_symbols=examined,
        generated_chunks=generated,
        definition_lines=definition_lines,
        excluded_module_lines=module_lines,
        excluded_counts=inventory.excluded_counts,
        truncated=bool(reasons),
        incomplete_reasons=tuple(reasons),
        parser=f"python_ast_{sys.version_info.major}_{sys.version_info.minor}",
        strategy=CHUNK_STRATEGY,
    )
    # 完整JSON含正文转义、来源和覆盖字段；超限拒绝整体，不能切JSON或删元数据。
    payload = json.dumps(
        asdict(result), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(payload.encode("utf-8")) > MAX_CHUNK_RESULT_BYTES:
        raise PythonChunksError(
            "python_chunks_result_too_large", "代码分块结果超过输出预算"
        )
    return result
