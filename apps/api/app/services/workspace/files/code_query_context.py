"""可信宿主的查询→授权召回→有界上下文串联，不发送最终提示。"""

from dataclasses import dataclass

import httpx

from app.services.runtime.execution.execution_threads import ExecutionThreads
from pydantic import ValidationError

from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.model.query_embeddings import _prepare_query
from app.services.workspace.files.code_context import (
    CodeContextBudget,
    CodeContextError,
    CodeContextPackage,
    build_code_context,
)
from app.services.workspace.files.code_query_search import (
    CodeQuerySearchResult,
    search_code_query,
)
from app.services.workspace.files.code_vector_search import CodeVectorSearchResult


@dataclass(frozen=True)
class CodeQueryContextResult:
    # 此处只保留本次查询的已报告用量；不与历史代码生成用量相加。
    query_sha256: str
    requested_model: str
    response_model: str
    prompt_tokens: int | None
    total_tokens: int | None
    request_count: int

    # 原批次用量/覆盖与本轮上下文省略保留在独立包中。
    # 成功结果不重复携带全部召回正文、查询正文、向量或凭证。
    context: CodeContextPackage
    query_source: str = "query_embedding"
    source: str = "code_query_context"


def _validated_budget(budget: CodeContextBudget | None) -> CodeContextBudget:
    if budget is None:
        return CodeContextBudget()
    if type(budget) is not CodeContextBudget:
        raise CodeContextError("code_context_budget_invalid")

    try:
        # 必须从字段重新校验；直接验证模型实例可能跳过检查，
        # 而model_copy/model_construct可绕过原构造约束。重新创建也隔离调用方对象。
        # 不序列化非法字段，避免序列化警告反射传入内容。
        return CodeContextBudget.model_validate(
            {
                "max_chunks": budget.max_chunks,
                "max_chars": budget.max_chars,
                "max_bytes": budget.max_bytes,
            }
        )
    except (ValidationError, AttributeError):
        raise CodeContextError("code_context_budget_invalid") from None


async def build_code_query_context(
    query: str,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    batch_id: str,
    config: EmbeddingConfig,
    response_model: str,
    top_k: int = 5,
    budget: CodeContextBudget | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    execution_threads: ExecutionThreads | None = None,
) -> CodeQueryContextResult:
    """宿主显式选择查询发送范围、批次/空间和预算，不凭旧结果授予许可。"""
    # 预算范围和查询文本在任何数据库/HTTP操作前检查。
    # 这里只能确认预算合法，是否能容纳真实来源声明需等召回结果再判断。
    active_budget = _validated_budget(budget)
    query_sha256 = _prepare_query(query)

    # 事务边界由既有串联服务管理：先短事务授权，关闭Session后调用模型，
    # HTTP资源完整关闭后再重新授权召回；此await不新增外层Session或重试。
    searched = await search_code_query(
        query,
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        batch_id=batch_id,
        config=config,
        response_model=response_model,
        top_k=top_k,
        transport=transport,
        **({"execution_threads": execution_threads} if execution_threads is not None else {}),
    )

    # 装配边界核对当前调用的查询及目标，未知/错配结果不能变成上下文成功。
    # 模型空间不是授权，实际归属/绑定仍由刚结束的召回事务检查。
    if (
        type(searched) is not CodeQuerySearchResult
        or searched.query_sha256 != query_sha256
        or searched.requested_model != config.model
        or searched.response_model != response_model
        or type(searched.request_count) is not int
        or searched.request_count != 1
        or searched.source != "query_embedding"
        or type(searched.recall) is not CodeVectorSearchResult
        or searched.recall.batch_id != batch_id
        or searched.recall.space_id != code_embedding_space_id(config, response_model)
        or type(searched.recall.dimensions) is not int
        or searched.recall.dimensions != config.dimensions
        or type(searched.recall.top_k) is not int
        or searched.recall.top_k != top_k
        or type(searched.recall.metadata) is not dict
        or searched.recall.metadata.get("workspace_id") != workspace_id
        or searched.recall.metadata.get("task_id") != task_id
        or searched.recall.metadata.get("requested_model") != config.model
        or searched.recall.metadata.get("response_model") != response_model
    ):
        raise CodeContextError("code_context_query_result_invalid")

    # 缺失用量必须成对未知；已报告值为非负严格整数，不推算零或混合历史费用。
    prompt, total = searched.prompt_tokens, searched.total_tokens
    if not (prompt is None and total is None) and (
        type(prompt) is not int
        or type(total) is not int
        or prompt < 0
        or total < prompt
    ):
        raise CodeContextError("code_context_query_result_invalid")

    # 到这里两个短事务和HTTP均已退出；构建只消费本次普通快照。
    # 构建失败整次传播，不返回部分结果、放宽预算或回退旧上下文。
    context = build_code_context(searched.recall, budget=active_budget)
    return CodeQueryContextResult(
        query_sha256=searched.query_sha256,
        requested_model=searched.requested_model,
        response_model=searched.response_model,
        prompt_tokens=prompt,
        total_tokens=total,
        request_count=searched.request_count,
        context=context,
    )
