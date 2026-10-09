"""离线受控向量评测适配；不注册产品能力，不连接真实模型。"""

from hashlib import sha256
import json
import re

import httpx
from pydantic import SecretStr

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.embedding_config import EmbeddingConfig, EmbeddingError
from app.services.model.code_embeddings import code_embedding_space_id
from app.services.workspace.files.code_query_search import search_code_query
from app.services.workspace.files.code_retrieval_evaluation import Citation, Dataset, Prediction, Run, dataset_digest
from app.services.workspace.files.code_vector_search import CodeVectorSearchError, CodeVectorSearchResult, MAX_TOP_K

DIMENSIONS = 64
MODEL = "controlled-token-hash-v1"


def controlled_config() -> EmbeddingConfig:
    return EmbeddingConfig(api_key=SecretStr("controlled-fixture-only"), base_url="https://evaluation.invalid/v1", model=MODEL, dimensions=DIMENSIONS)


def controlled_vector(text: str) -> list[float]:
    # 固定SHA映射英文词，不使用query ID或gold；碰撞/中文漏检是该夹具的局限。
    vector = [0.0] * DIMENSIONS
    vector[0] = 0.01  # 空词查询也有合法非零向量；不会被假装成无结果。
    for term in set(re.findall(r"[a-z][a-z0-9]*", text.casefold())):
        index = 1 + int.from_bytes(sha256(term.encode()).digest()[:4], "big") % (DIMENSIONS - 1)
        vector[index] = 1.0
    return vector


def controlled_response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if payload["model"] != MODEL:
        raise ValueError("unexpected_controlled_model")
    return httpx.Response(200, json={
        "object": "list", "model": MODEL,
        "data": [{"object": "embedding", "index": index, "embedding": controlled_vector(text)} for index, text in enumerate(payload["input"])],
        # 不报告虚构的Token用量；上层保留unknown，不用0冒充真实成本。
    })


def map_recall(
    dataset: Dataset, texts: dict[str, str], result: CodeVectorSearchResult, *,
    workspace_id: str, task_id: str, batch_id: str, query_id: str,
) -> Prediction:
    """v1只接完整单块定义；未来分片策略须另行明确聚合，不能静默丢候选。"""
    metadata = result.metadata
    expected_files = {s.relative_path: s.file_sha256 for s in dataset.sources}
    files = metadata.get("files", [])
    if (
        set(texts) != {s.id for s in dataset.sources}
        or result.batch_id != batch_id or result.top_k != MAX_TOP_K
        or result.dimensions != DIMENSIONS or result.space_id != code_embedding_space_id(controlled_config(), MODEL)
        or metadata.get("workspace_id") != workspace_id or metadata.get("task_id") != task_id
        or metadata.get("dimensions") != DIMENSIONS or metadata.get("embedding_space_id") != result.space_id
        or metadata.get("requested_model") != MODEL or metadata.get("response_model") != MODEL
        or metadata.get("truncated") is not False or metadata.get("incomplete_reasons") != []
        or len(files) != len(expected_files)
        or {file["relative_path"]: file["sha256"] for file in files} != expected_files
        or result.batch_chunk_count != len(dataset.sources)
        or result.searchable_chunk_count != len(dataset.sources)
        or result.excluded_zero_chunk_count != 0 or result.omitted_by_top_k != 0
        or len(result.hits) != len(dataset.sources)
    ):
        raise ValueError("evaluation_snapshot_mismatch")
    sources = {(s.relative_path, s.symbol): s for s in dataset.sources}
    ranked = []
    for rank, hit in enumerate(result.hits, 1):
        chunk = hit.chunk
        symbol = chunk["symbol"]
        source = sources.get((symbol["relative_path"], symbol["qualified_name"]))
        if source is None:
            raise ValueError("evaluation_unknown_source")
        expected_text = texts[source.id]
        if (
            hit.rank != rank or source.id in ranked
            or symbol["sha256"] != source.file_sha256
            or (symbol["start_line"], symbol["end_line"]) != (source.start_line, source.end_line)
            or symbol["definition_line"] != source.start_line or symbol["name"] != source.symbol
            or chunk["start_line"] != source.start_line or chunk["start_column"] != 1
            or chunk["end_line"] != source.start_line + expected_text.count("\n")
            or chunk["end_column"] != len(expected_text.rsplit("\n", 1)[-1]) + 1
            or chunk["text"] != expected_text
            or chunk["text_sha256"] != sha256(expected_text.encode()).hexdigest()
            or chunk["part_index"] != 1 or chunk["part_count"] != 1 or chunk["split_reasons"] != []
        ):
            raise ValueError("evaluation_source_mismatch")
        ranked.append(source.id)
    first = next(s for s in dataset.sources if s.id == ranked[0])
    return Prediction(query_id=query_id, status="ok", ranked_source_ids=ranked, citations=[Citation(source_id=first.id, line=first.start_line)])


async def collect_vector_run(
    dataset: Dataset, texts: dict[str, str], *, user_id: int, workspace_id: str, task_id: str,
    batch_id: str, transport: httpx.AsyncBaseTransport,
) -> Run:
    if len(dataset.sources) > MAX_TOP_K:
        raise ValueError("evaluation_corpus_too_large")
    predictions = []
    for query in dataset.queries:
        # 每题走生产发送前授权→事务外HTTP→再次授权/真实排序，不复用旧查询结果。
        try:
            response = await search_code_query(
                query.query, user_id=user_id, workspace_id=workspace_id, task_id=task_id,
                batch_id=batch_id, config=controlled_config(), response_model=MODEL,
                top_k=MAX_TOP_K, transport=transport,
            )
        except (EmbeddingError, WorkspaceNotAccessibleError, CodeVectorSearchError):
            predictions.append(Prediction(query_id=query.id, status="error", ranked_source_ids=[], citations=[]))
            continue
        # 映射失败是评测数据不兼容，不能转换成普通漏检或用gold修补结果。
        if response.query_sha256 != sha256(query.query.encode()).hexdigest():
            raise ValueError("evaluation_query_mismatch")
        predictions.append(map_recall(dataset, texts, response.recall, workspace_id=workspace_id, task_id=task_id, batch_id=batch_id, query_id=query.id))
    return Run(schema_version=1, dataset_sha256=dataset_digest(dataset), strategy="controlled-token-hash-pgvector-v1", citation_origin="retrieval_reference", predictions=predictions)
