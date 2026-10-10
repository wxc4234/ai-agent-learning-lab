"""显式已存批次的授权词面召回；不读文件、不请求模型、不修改索引。"""

from copy import deepcopy
from dataclasses import dataclass
from itertools import pairwise
import json
import re

from app.database import SessionLocal
from app.repositories.workspace.code_embedding_repository import read_owned_code_embedding_batch, read_batch_keyword_chunks
from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task
from app.services.workspace.files.code_vector_storage import _Chunk, _File

MAX_QUERY_CHARS = 2000
MAX_QUERY_BYTES = 4096
MAX_QUERY_TERMS = 32
MAX_BATCH_CHUNKS = 20
MAX_TOP_K = 20


class CodeKeywordSearchError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CodeKeywordHit:
    rank: int
    # 分数是命中的不同词项数量；不是概率，也不能直接与余弦距离相加。
    score: int
    matched_terms: tuple[str, ...]
    chunk: dict


@dataclass(frozen=True)
class CodeKeywordSearchResult:
    batch_id: str
    space_id: str
    top_k: int
    batch_chunk_count: int
    matched_chunk_count: int
    omitted_by_top_k: int
    query_terms: tuple[str, ...]
    metadata: dict
    hits: tuple[CodeKeywordHit, ...]
    strategy: str = "unicode-word-overlap-v1"


def _terms(text: str) -> set[str]:
    # 下划线拆词，保留Unicode文字/数字；中文连续文本不假装已做分词。
    return set(re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE))


def _validate_chunks(metadata: dict, chunks: list[dict]) -> None:
    """复用存储的路径/摘要/坐标校验，再复核整个词面快照的来源与分片。"""
    files = [_File.model_validate(file) for file in metadata["files"]]
    file_map = {file.relative_path: file for file in files}
    if not 1 <= len(files) <= 20 or len(file_map) != len(files):
        raise ValueError("files_invalid")
    validated = [_Chunk.model_validate_json(json.dumps(chunk, ensure_ascii=False)) for chunk in chunks]
    if len({chunk.chunk_id for chunk in validated}) != len(validated):
        raise ValueError("duplicate_chunk")
    for chunk in validated:
        source = file_map.get(chunk.symbol.relative_path)
        if source is None or source.sha256 != chunk.symbol.sha256 or len(chunk.text.encode()) > source.byte_count:
            raise ValueError("chunk_file_mismatch")
    ordered = sorted(validated, key=lambda c: (c.symbol.relative_path, c.start_line, c.start_column))
    for previous, current in pairwise(ordered):
        if (previous.symbol.relative_path == current.symbol.relative_path
                and (previous.end_line, previous.end_column) > (current.start_line, current.start_column)):
            raise ValueError("overlapping_chunks")
    groups: dict[tuple, list[_Chunk]] = {}
    for chunk in ordered:
        group = groups.setdefault(tuple(chunk.symbol.model_dump().values()), [])
        if group and (group[-1].part_index >= chunk.part_index or group[-1].part_count != chunk.part_count
                      or group[-1].split_reasons != chunk.split_reasons):
            raise ValueError("inconsistent_parts")
        group.append(chunk)
    if not metadata["truncated"] and any(len(parts) != parts[0].part_count or parts[0].part_index != 1
                                         or parts[-1].part_index != parts[0].part_count for parts in groups.values()):
        raise ValueError("missing_parts")


def validate_code_keyword_query(query: str, *, top_k: int, space_id: str) -> set[str]:
    # 明显非法输入在事务外拒绝；不把原始查询或宿主路径写入错误消息。
    try:
        if (type(query) is not str or not 1 <= len(query) <= MAX_QUERY_CHARS
                or len(query.encode()) > MAX_QUERY_BYTES
                or any(ord(c) < 32 or ord(c) == 127 for c in query)
                or type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K
                or type(space_id) is not str or re.fullmatch(r"[a-f0-9]{64}", space_id) is None):
            raise ValueError()
        terms = _terms(query)
        if not 1 <= len(terms) <= MAX_QUERY_TERMS:
            raise ValueError()
    except (ValueError, UnicodeError, TypeError):
        raise CodeKeywordSearchError("code_keyword_query_invalid") from None

    return terms


def search_code_keyword_batch(
    query: str, *, user_id: int, workspace_id: str, task_id: str,
    batch_id: str, space_id: str, top_k: int = 5,
) -> CodeKeywordSearchResult:
    terms = validate_code_keyword_query(query, top_k=top_k, space_id=space_id)

    with SessionLocal.begin() as session:
        # 授权、目录绑定/修订复核、读取和排序共用归属锁；无外部I/O或写入。
        lock_owned_proposal_task(session, user_id=user_id, workspace_id=workspace_id, task_id=task_id)
        batch = read_owned_code_embedding_batch(session, user_id=user_id, workspace_id=workspace_id,
                                               task_id=task_id, batch_id=batch_id, space_id=space_id)
        try:
            metadata = deepcopy(batch.source_metadata)
            if (not 1 <= batch.chunk_count <= MAX_BATCH_CHUNKS
                    or type(metadata) is not dict or len(json.dumps(metadata).encode()) > 128 * 1024
                    or metadata.get("workspace_id") != workspace_id or metadata.get("task_id") != task_id
                    or metadata.get("embedding_space_id") != space_id or metadata.get("dimensions") != batch.dimensions
                    or type(metadata.get("truncated")) is not bool or type(metadata.get("incomplete_reasons")) is not list
                    or metadata["truncated"] != bool(metadata["incomplete_reasons"])):
                raise ValueError()
            rows = read_batch_keyword_chunks(session, batch=batch)
            if len(rows) != batch.chunk_count or [r["ordinal"] for r in rows] != list(range(batch.chunk_count)):
                raise ValueError()
            chunks = []
            for row in rows:
                if (row["content"] is None or row["chunk_metadata"] is None
                        or type(row["chunk_metadata"]) is not dict or row["chunk_metadata"].get("chunk_id") != row["chunk_id"]):
                    raise ValueError()
                chunk = deepcopy(row["chunk_metadata"])
                chunk["text"] = row["content"]
                chunks.append(chunk)
            _validate_chunks(metadata, chunks)
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise CodeKeywordSearchError("code_keyword_batch_inconsistent") from None
        # 所有候选均验证后才匹配/截取；无关坏行也不能被Top K掩盖。
        matched = []
        for ordinal, chunk in enumerate(chunks):
            symbol = chunk["symbol"]
            haystack = " ".join((symbol["relative_path"], symbol["qualified_name"], chunk["text"]))
            found = tuple(sorted(terms & _terms(haystack)))
            if found:
                matched.append((len(found), ordinal, chunk["chunk_id"], found, chunk))
        # 命中不同词数降序，原始序号、chunk_id升序，重复词不刷分。
        matched.sort(key=lambda item: (-item[0], item[1], item[2]))
        hits = tuple(CodeKeywordHit(rank, score, found, chunk)
                     for rank, (score, _, _, found, chunk) in enumerate(matched[:top_k], 1))
        result = CodeKeywordSearchResult(batch.external_id, batch.space_id, top_k, batch.chunk_count,
            len(matched), max(0, len(matched) - top_k), tuple(sorted(terms)), metadata, hits)
    # 返回脱离Session的快照；旧结果不授权未来的文件读取或模型发送。
    return result
