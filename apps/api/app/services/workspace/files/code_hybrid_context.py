"""混合召回快照的纯内存预算适配；不打开事务或授予模型发送许可。"""

from dataclasses import asdict
from fractions import Fraction
import json
from typing import Literal

from pydantic import Field, model_validator

from app.services.workspace.files.code_context import (
    CodeContextBudget, CodeContextError, CodeContextPackage, Count, MAX_SNAPSHOT_BYTES,
    _Metadata, _build_context,
)
from app.services.workspace.files.code_hybrid_search import CodeHybridSearchResult
from app.services.workspace.files.code_keyword_search import _terms
from app.services.workspace.files.code_vector_storage import Digest, _Chunk, _Record


class _HybridHit(_Record):
    rank: int = Field(ge=1, le=20)
    rrf_score: float = Field(gt=0, allow_inf_nan=False)
    keyword_rank: int | None = Field(ge=1, le=20)
    vector_rank: int | None = Field(ge=1, le=20)
    matched_terms: tuple[str, ...] = Field(max_length=32)
    keyword_score: int | None = Field(ge=1, le=32)
    vector_distance: float | None = Field(ge=0, le=2, allow_inf_nan=False)
    chunk: _Chunk

    def exact_score(self) -> Fraction:
        return sum((Fraction(1, 60 + rank) for rank in (self.keyword_rank, self.vector_rank)
                    if rank is not None), Fraction(0))

    @model_validator(mode="after")
    def evidence(self) -> "_HybridHit":
        if self.keyword_rank is None:
            if self.matched_terms or self.keyword_score is not None:
                raise ValueError("unexpected_keyword_evidence")
        else:
            terms = set(self.matched_terms)
            haystack = f"{self.chunk.symbol.relative_path} {self.chunk.symbol.qualified_name} {self.chunk.text}"
            if (not terms or self.matched_terms != tuple(sorted(terms))
                    or self.keyword_score != len(terms) or not terms <= _terms(haystack)):
                raise ValueError("invalid_keyword_evidence")
        if ((self.vector_rank is None) != (self.vector_distance is None)
                or self.keyword_rank is None and self.vector_rank is None
                or self.rrf_score != float(self.exact_score())):
            raise ValueError("invalid_rrf_evidence")
        return self


class _HybridSnapshot(_Record):
    batch_id: str = Field(min_length=1, max_length=100)
    space_id: Digest
    top_k: int = Field(ge=1, le=20)
    batch_chunk_count: int = Field(ge=1, le=20)
    keyword_candidate_count: Count
    vector_candidate_count: Count
    excluded_zero_chunk_count: Count
    fused_candidate_count: Count
    omitted_by_top_k: Count
    metadata: _Metadata
    hits: tuple[_HybridHit, ...] = Field(max_length=20)
    rrf_k: Literal[60]
    strategy: Literal["single-batch-rrf-v1"]

    @property
    def dimensions(self) -> int:
        return self.metadata.dimensions

    @model_validator(mode="after")
    def consistency(self) -> "_HybridSnapshot":
        if (self.space_id != self.metadata.embedding_space_id
                or self.vector_candidate_count + self.excluded_zero_chunk_count != self.batch_chunk_count
                or not max(self.keyword_candidate_count, self.vector_candidate_count) <= self.fused_candidate_count
                       <= min(self.batch_chunk_count, self.keyword_candidate_count + self.vector_candidate_count)
                or len(self.hits) != min(self.top_k, self.fused_candidate_count)
                or self.omitted_by_top_k != self.fused_candidate_count - len(self.hits)):
            raise ValueError("invalid_hybrid_counts_or_space")
        files = {file.relative_path: file for file in self.metadata.files}
        ids: set[str] = set()
        keyword_ranks: set[int] = set()
        vector_ranks: set[int] = set()
        orders = []
        # 所有输入先验证，包括预算外的尾部；融合通道已去重，重复ID不是正常省略。
        for rank, hit in enumerate(self.hits, 1):
            chunk = hit.chunk
            file = files.get(chunk.symbol.relative_path)
            if (hit.rank != rank or chunk.chunk_id in ids or file is None
                    or file.sha256 != chunk.symbol.sha256 or len(chunk.text.encode()) > file.byte_count):
                raise ValueError("invalid_hybrid_source")
            ids.add(chunk.chunk_id)
            for candidate_rank, count, seen in (
                (hit.keyword_rank, self.keyword_candidate_count, keyword_ranks),
                (hit.vector_rank, self.vector_candidate_count, vector_ranks),
            ):
                if candidate_rank is not None:
                    if candidate_rank > count or candidate_rank in seen:
                        raise ValueError("invalid_channel_rank")
                    seen.add(candidate_rank)
            orders.append((-hit.exact_score(), chunk.symbol.relative_path, chunk.start_line, chunk.start_column, chunk.chunk_id))
        # 未返回候选最多补充omitted_by_top_k个通道排名，计数不能凭空多出。
        if any(not len(seen) <= count <= len(seen) + self.omitted_by_top_k
               for seen, count in ((keyword_ranks, self.keyword_candidate_count),
                                   (vector_ranks, self.vector_candidate_count))):
            raise ValueError("invalid_observed_channel_counts")
        if orders != sorted(orders):
            raise ValueError("invalid_hybrid_order")
        return self


def build_code_hybrid_context(
    result: CodeHybridSearchResult, *, budget: CodeContextBudget | None = None,
) -> CodeContextPackage:
    active = CodeContextBudget() if budget is None else budget
    if type(active) is not CodeContextBudget:
        raise CodeContextError("code_context_budget_invalid")
    try:
        if type(result) is not CodeHybridSearchResult:
            raise ValueError("invalid_result_type")
        # JSON往返隔离嵌套对象；限制序列化后的规模，不声称硬内存隔离。
        raw = json.dumps(asdict(result), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise CodeContextError("code_context_snapshot_too_large")
        snapshot = _HybridSnapshot.model_validate_json(raw)
    except CodeContextError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise CodeContextError("code_context_snapshot_invalid") from None
    # 此处只有内存操作；原批次metadata中的用量是历史代码Embedding用量。
    return _build_context(snapshot, active, recall_summary={
        "batch_chunk_count": snapshot.batch_chunk_count,
        "keyword_candidate_count": snapshot.keyword_candidate_count,
        "vector_candidate_count": snapshot.vector_candidate_count,
        "excluded_zero_chunk_count": snapshot.excluded_zero_chunk_count,
        "fused_candidate_count": snapshot.fused_candidate_count,
        "omitted_by_top_k": snapshot.omitted_by_top_k,
    }, source="bounded_hybrid_code_context")
