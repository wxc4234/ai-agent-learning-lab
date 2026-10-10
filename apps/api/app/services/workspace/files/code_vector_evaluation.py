"""离线受控向量评测适配；不注册产品能力，不连接真实模型。"""

from hashlib import sha256
import json
import re

import httpx
from pydantic import SecretStr

from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.model.embedding_config import EmbeddingConfig, EmbeddingError
from app.services.workspace.files.code_evaluation_mapping import map_recall as map_provider_recall
from app.services.workspace.files.code_query_search import search_code_query
from app.services.workspace.files.code_retrieval_evaluation import Dataset, Prediction, Run, dataset_digest
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
    # 原受控入口仍固定其模型空间；通用映射不再依赖该夹具。
    return map_provider_recall(dataset, texts, result, workspace_id=workspace_id,
                               task_id=task_id, batch_id=batch_id, query_id=query_id,
                               config=controlled_config(), response_model=MODEL)


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
