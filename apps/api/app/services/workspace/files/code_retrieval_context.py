"""内部候选选择→授权查询上下文组合，不生成索引或发送最终提示。"""

import asyncio
from functools import partial
from dataclasses import dataclass
from typing import Literal

import httpx

from app.services.runtime.execution.execution_threads import ExecutionThreads

from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.model.query_embeddings import _prepare_query
from app.services.workspace.files.code_batch_selection import (
    CodeBatchSelectionResult,
    _prepare_config,
    select_code_embedding_batch,
)
from app.services.workspace.files.code_context import CodeContextBudget, CodeContextError
from app.services.workspace.files.code_query_context import (
    CodeQueryContextResult,
    _validated_budget,
    build_code_query_context,
)
from app.services.workspace.files.code_vector_search import MAX_TOP_K, CodeVectorSearchError


@dataclass(frozen=True)
class CodeRetrievalContextResult:
    # 未找到与成功但无命中分开；异常不包装成其中任何一种成功结果。
    status: Literal["not_found_in_window", "context_ready"]
    selection: CodeBatchSelectionResult
    # 查询用量、原覆盖和构建省略由既有结果保留，不重复拼装或相加。
    query_context: CodeQueryContextResult | None
    source: Literal["code_retrieval_context"] = "code_retrieval_context"


def _check_cancelled() -> None:
    # 防止内部依赖吞掉取消后发布迟到结果；不承诺中断同步数据库操作。
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()


async def build_code_retrieval_context(
    query: str,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    config: EmbeddingConfig,
    top_k: int = 5,
    budget: CodeContextBudget | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    execution_threads: ExecutionThreads | None = None,
) -> CodeRetrievalContextResult:
    """可信宿主指定查询、身份及独立配置；不接受模型自行选择批次。"""
    _check_cancelled()
    # 所有与是否存在候选无关的参数先校验，坏输入不能被空窗口掩盖。
    active_budget = _validated_budget(budget)
    _prepare_query(query)
    if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
        raise CodeVectorSearchError("code_embedding_query_invalid")
    active = _prepare_config(config)

    # 事务边界：摘要服务自行开启并退出短事务；此处不持有外层Session。
    select_batch = partial(select_code_embedding_batch,
        user_id=user_id, workspace_id=workspace_id, task_id=task_id, config=active
    )
    # 聊天请求把同步SQL交给已跟踪线程；取消后宿主仍等待线程收尾。
    selection = select_batch() if execution_threads is None else await execution_threads.run(select_batch)
    _check_cancelled()
    if (
        type(selection) is not CodeBatchSelectionResult
        or selection.workspace_id != workspace_id
        or selection.task_id != task_id
    ):
        raise CodeContextError("code_context_selection_invalid")
    if selection.status == "not_found_in_window" and selection.selected is None:
        return CodeRetrievalContextResult(
            status="not_found_in_window", selection=selection, query_context=None
        )
    selected = selection.selected
    if (
        selection.status != "selected"
        or selected is None
        or selected.requested_model != active.model
        or selected.dimensions != active.dimensions
        or selected.space_id != code_embedding_space_id(active, selected.response_model)
    ):
        raise CodeContextError("code_context_selection_invalid")

    # 候选不是许可：既有链路发送前重新授权指定批次，HTTP退出后核对
    # 实际供应商版本并再次授权召回。期间撤销/改绑定/删批次均可导致失败。
    queried = await build_code_query_context(
        query, user_id=user_id, workspace_id=workspace_id, task_id=task_id,
        batch_id=selected.batch_id, config=active, response_model=selected.response_model,
        top_k=top_k, budget=active_budget, transport=transport,
        **({"execution_threads": execution_threads} if execution_threads is not None else {}),
    )
    _check_cancelled()
    # 无命中仍是合法上下文成功；不换批次、不重试、不自动创建索引。
    return CodeRetrievalContextResult(
        status="context_ready", selection=selection, query_context=queried
    )
