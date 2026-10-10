"""显式单批：查询生成→当前授权RRF→有界上下文；不发送最终聊天提示。"""

import asyncio
from functools import partial

import httpx

from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.model.query_embeddings import _prepare_query, generate_query_embedding
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.workspace.files.code_context import CodeContextBudget, CodeContextError
from app.services.workspace.files.code_hybrid_context import build_code_hybrid_context
from app.services.workspace.files.code_hybrid_search import CodeHybridSearchResult, search_code_hybrid_batch
from app.services.workspace.files.code_keyword_search import validate_code_keyword_query
from app.services.workspace.files.code_query_context import CodeQueryContextResult, _validated_budget
from app.services.workspace.files.code_query_search import _preflight_batch, _check_generated_query
from app.services.workspace.files.code_vector_search import CodeVectorSearchError


async def build_code_hybrid_query_context(
    query: str, *, user_id: int, workspace_id: str, task_id: str, batch_id: str,
    config: EmbeddingConfig, response_model: str, top_k: int = 5,
    budget: CodeContextBudget | None = None, transport: httpx.AsyncBaseTransport | None = None,
    execution_threads: ExecutionThreads | None = None,
) -> CodeQueryContextResult:
    # 两种查询契约、预算及目标空间均先验证，避免已知非法请求产生模型费用。
    active = _validated_budget(budget)
    query_sha256 = _prepare_query(query)
    try:
        if type(response_model) is not str:
            raise ValueError()
        EmbeddingConfig.validate_model(response_model)
        space_id = code_embedding_space_id(config, response_model)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise CodeVectorSearchError("code_embedding_query_invalid") from None
    validate_code_keyword_query(query, top_k=top_k, space_id=space_id)
    scope: dict = {"user_id": user_id, "workspace_id": workspace_id, "task_id": task_id, "batch_id": batch_id}
    preflight = partial(_preflight_batch, **scope, config=config, response_model=response_model, space_id=space_id)
    if execution_threads is None:
        preflight()
    else:
        await execution_threads.run(preflight)
    # 此时授权事务已退出；让已请求的取消先被观察，随后只发送本次query一次。
    await asyncio.sleep(0)
    generated = await generate_query_embedding(query, config=config, transport=transport)
    _check_generated_query(generated, query_sha256=query_sha256, config=config,
                           response_model=response_model, space_id=space_id)
    # HTTP已关闭；模型等待期间的撤销由接下来的两条召回通道重新检查。
    await asyncio.sleep(0)
    search = partial(search_code_hybrid_batch, query, **scope, config=config,
                     response_model=response_model, query_vector=generated.vector, top_k=top_k)
    recalled = search() if execution_threads is None else await execution_threads.run(search)
    if (type(recalled) is not CodeHybridSearchResult or recalled.batch_id != batch_id
            or recalled.space_id != space_id or type(recalled.top_k) is not int or recalled.top_k != top_k
            or type(recalled.metadata) is not dict
            or any(recalled.metadata.get(key) != expected for key, expected in (
                ("workspace_id", workspace_id), ("task_id", task_id), ("dimensions", config.dimensions),
                ("requested_model", config.model), ("response_model", response_model)))):
        raise CodeContextError("code_context_query_result_invalid")
    await asyncio.sleep(0)
    # 所有事务和HTTP资源关闭后才纯内存构建；失败不重试、不放宽预算或回退旧包。
    context = build_code_hybrid_context(recalled, budget=active)
    return CodeQueryContextResult(generated.query_sha256, generated.requested_model,
        generated.response_model, generated.prompt_tokens, generated.total_tokens,
        generated.request_count, context, source="code_hybrid_query_context")
