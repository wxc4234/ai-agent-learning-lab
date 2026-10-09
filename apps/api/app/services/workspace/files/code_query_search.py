"""授权单查询生成与单批精确召回；模型等待与数据库事务分离。"""

from functools import partial
from dataclasses import dataclass

import httpx

from app.services.runtime.execution.execution_threads import ExecutionThreads

from app.database import SessionLocal
from app.repositories.workspace.code_embedding_repository import (
    read_owned_code_embedding_batch,
)
from app.repositories.workspace.file_edit_proposal_repository import (
    lock_owned_proposal_task,
)
from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.model.query_embeddings import (
    QueryEmbedding,
    _prepare_query,
    generate_query_embedding,
)
from app.services.workspace.files.code_vector_search import (
    MAX_BATCH_CHUNKS,
    MAX_TOP_K,
    CodeVectorSearchError,
    CodeVectorSearchResult,
    search_code_embedding_batch,
)


@dataclass(frozen=True)
class CodeQuerySearchResult:
    # 只投影查询来源和已报告用量，不重复返回查询正文、向量或配置凭证。
    query_sha256: str
    requested_model: str
    response_model: str
    prompt_tokens: int | None
    total_tokens: int | None
    request_count: int
    recall: CodeVectorSearchResult
    source: str = "query_embedding"


def _preflight_batch(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    batch_id: str,
    config: EmbeddingConfig,
    response_model: str,
    space_id: str,
) -> None:
    # 第一段短事务只核对当前授权及批次声明，不读取正文或计算距离。
    # 成功退出Session后才能发送查询；检查失败或退出失败均不能继续。
    with SessionLocal.begin() as session:
        lock_owned_proposal_task(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        batch = read_owned_code_embedding_batch(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            batch_id=batch_id,
            space_id=space_id,
        )
        metadata = batch.source_metadata
        if (
            batch.dimensions != config.dimensions
            or not 1 <= batch.chunk_count <= MAX_BATCH_CHUNKS
            or type(metadata) is not dict
            or metadata.get("workspace_id") != workspace_id
            or metadata.get("task_id") != task_id
            or metadata.get("dimensions") != config.dimensions
            or metadata.get("embedding_space_id") != space_id
            or metadata.get("requested_model") != config.model
            or metadata.get("response_model") != response_model
            or type(metadata.get("truncated")) is not bool
            or type(metadata.get("incomplete_reasons")) is not list
            or metadata["truncated"] != bool(metadata["incomplete_reasons"])
        ):
            raise CodeVectorSearchError("code_embedding_batch_inconsistent")


def _check_generated_query(
    generated: QueryEmbedding,
    *,
    query_sha256: str,
    config: EmbeddingConfig,
    response_model: str,
    space_id: str,
) -> None:
    # 不把一次模型成功当成指定空间的证明；同时核对来源、版本及用量状态。
    # 向量的float32、零向量和距离检查交给现有召回入口，不在这里静默修复。
    if (
        type(generated) is not QueryEmbedding
        or generated.query_sha256 != query_sha256
        or generated.requested_model != config.model
        or generated.response_model != response_model
        or type(generated.dimensions) is not int
        or generated.dimensions != config.dimensions
        or generated.embedding_space_id != space_id
        or type(generated.request_count) is not int
        or generated.request_count != 1
        or generated.source != "query_embedding"
    ):
        raise CodeVectorSearchError("code_embedding_query_result_invalid")

    # 两项用量都未知或都为已报告非负整数；未知不能伪装成零。
    prompt, total = generated.prompt_tokens, generated.total_tokens
    if prompt is None and total is None:
        return
    if (
        type(prompt) is not int
        or type(total) is not int
        or prompt < 0
        or total < prompt
    ):
        raise CodeVectorSearchError("code_embedding_query_result_invalid")


async def search_code_query(
    query: str,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    batch_id: str,
    config: EmbeddingConfig,
    response_model: str,
    top_k: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
    execution_threads: ExecutionThreads | None = None,
) -> CodeQuerySearchResult:
    """可信宿主显式选定查询发送范围、批次及预期报告版本。"""
    query_sha256 = _prepare_query(query)
    try:
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            raise ValueError("invalid_top_k")
        if type(response_model) is not str:
            raise ValueError("invalid_response_model")
        EmbeddingConfig.validate_model(response_model)
        space_id = code_embedding_space_id(config, response_model)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise CodeVectorSearchError("code_embedding_query_invalid") from None

    preflight = partial(_preflight_batch,
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        batch_id=batch_id,
        config=config,
        response_model=response_model,
        space_id=space_id,
    )

    if execution_threads is None:
        preflight()
    else:
        await execution_threads.run(preflight)

    # 事务边界：此await前第一段Session已关闭，此处仅有一次独立HTTP请求。
    # 不重试；失败、超时和取消原样走既有生成契约，绝不回退到旧向量。
    generated = await generate_query_embedding(
        query, config=config, transport=transport
    )
    _check_generated_query(
        generated,
        query_sha256=query_sha256,
        config=config,
        response_model=response_model,
        space_id=space_id,
    )

    # 第二段事务由召回服务创建，重新锁定并核对当前归属、绑定和同一批次。
    # 模型等待时撤销归属、重绑定或删除任务，均不能凭第一段检查返回旧内容。
    search = partial(search_code_embedding_batch,
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        batch_id=batch_id,
        config=config,
        response_model=response_model,
        query_vector=generated.vector,
        top_k=top_k,
    )
    recall = search() if execution_threads is None else await execution_threads.run(search)
    # 召回成功退出事务后才发布结果；后置拒绝也无法撤销已经发生的模型用量。
    return CodeQuerySearchResult(
        query_sha256=generated.query_sha256,
        requested_model=generated.requested_model,
        response_model=generated.response_model,
        prompt_tokens=generated.prompt_tokens,
        total_tokens=generated.total_tokens,
        request_count=generated.request_count,
        recall=recall,
    )
