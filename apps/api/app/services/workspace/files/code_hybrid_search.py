"""显式单批的内部RRF融合；消费现成查询向量，不发送模型请求。"""

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction

from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.workspace.files.code_keyword_search import search_code_keyword_batch, _validate_chunks
from app.services.workspace.files.code_vector_search import search_code_embedding_batch

RRF_K = 60
MAX_CANDIDATES = 20


class CodeHybridSearchError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CodeHybridHit:
    rank: int
    # 排序使用精确分数，展示才转换为float；RRF不是相关性概率。
    rrf_score: float
    keyword_rank: int | None
    vector_rank: int | None
    matched_terms: tuple[str, ...]
    keyword_score: int | None
    vector_distance: float | None
    chunk: dict


@dataclass(frozen=True)
class CodeHybridSearchResult:
    batch_id: str
    space_id: str
    top_k: int
    batch_chunk_count: int
    keyword_candidate_count: int
    vector_candidate_count: int
    excluded_zero_chunk_count: int
    fused_candidate_count: int
    omitted_by_top_k: int
    metadata: dict
    hits: tuple[CodeHybridHit, ...]
    rrf_k: int = RRF_K
    strategy: str = "single-batch-rrf-v1"


def search_code_hybrid_batch(
    query: str, *, user_id: int, workspace_id: str, task_id: str, batch_id: str,
    config: EmbeddingConfig, response_model: str, query_vector: tuple[float, ...], top_k: int = 5,
) -> CodeHybridSearchResult:
    if type(top_k) is not int or not 1 <= top_k <= MAX_CANDIDATES:
        raise CodeHybridSearchError("code_hybrid_query_invalid")
    space_id = code_embedding_space_id(config, response_model)
    scope: dict = {"user_id": user_id, "workspace_id": workspace_id, "task_id": task_id, "batch_id": batch_id}
    # 两个独立短事务：关键词先校验全部正文，向量随后重新授权当前归属/修订。
    # 批次为不可变追加快照；没有跨事务锁或外部I/O，旧结果不授权后续发送。
    lexical = search_code_keyword_batch(query, **scope, space_id=space_id, top_k=MAX_CANDIDATES)
    semantic = search_code_embedding_batch(**scope, config=config, response_model=response_model,
                                          query_vector=query_vector, top_k=MAX_CANDIDATES)
    if (lexical.batch_id != semantic.batch_id or lexical.space_id != semantic.space_id
            or lexical.metadata != semantic.metadata or lexical.batch_chunk_count != semantic.batch_chunk_count
            or lexical.omitted_by_top_k or semantic.omitted_by_top_k):
        raise CodeHybridSearchError("code_hybrid_source_inconsistent")
    keywords = {hit.chunk["chunk_id"]: hit for hit in lexical.hits}
    vectors = {hit.chunk["chunk_id"]: hit for hit in semantic.hits}
    chunks = {key: hit.chunk for key, hit in keywords.items()}
    for key, hit in vectors.items():
        if key in chunks and chunks[key] != hit.chunk:
            raise CodeHybridSearchError("code_hybrid_source_inconsistent")
        chunks[key] = hit.chunk
    # 联集可能缺少零向量且无词面命中的片段；这里只校验已返回来源的一致性。
    # 原始完整覆盖已由第一通道验证，输出metadata仍保留原truncated值。
    try:
        _validate_chunks({**lexical.metadata, "truncated": True}, list(chunks.values()))
    except (ValueError, TypeError, KeyError):
        raise CodeHybridSearchError("code_hybrid_source_inconsistent") from None
    scores = {
        key: (Fraction(1, RRF_K + keywords[key].rank) if key in keywords else Fraction(0))
             + (Fraction(1, RRF_K + vectors[key].rank) if key in vectors else Fraction(0))
        for key in chunks
    }
    # 同分使用稳定来源坐标及ID，不依赖字典顺序或浮点舍入。
    def order(key):
        chunk = chunks[key]
        return (-scores[key], chunk["symbol"]["relative_path"], chunk["start_line"], chunk["start_column"], key)
    selected = sorted(chunks, key=order)[:top_k]
    hits = []
    for rank, key in enumerate(selected, 1):
        keyword = keywords.get(key)
        vector = vectors.get(key)
        hits.append(CodeHybridHit(rank, float(scores[key]), keyword.rank if keyword else None,
            vector.rank if vector else None, keyword.matched_terms if keyword else (),
            keyword.score if keyword else None, vector.distance if vector else None, deepcopy(chunks[key])))
    return CodeHybridSearchResult(lexical.batch_id, space_id, top_k, lexical.batch_chunk_count,
        len(keywords), len(vectors), semantic.excluded_zero_chunk_count, len(chunks),
        len(chunks) - len(hits), deepcopy(lexical.metadata), tuple(hits))
