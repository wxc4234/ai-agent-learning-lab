"""显式授权分块→生成→保存；无HTTP入口，不自动索引或重试。"""

import asyncio
from dataclasses import dataclass

import httpx

from app.services.model.code_embeddings import CodeEmbeddings, generate_code_embeddings
from app.services.model.embedding_config import EmbeddingConfig, load_embedding_config
from app.services.workspace.files.code_vector_storage import (
    CodeVectorStorageError,
    SavedCodeEmbeddingBatch,
    capture_code_embedding_target,
    save_code_embedding_batch,
)
from app.services.workspace.files.python_chunks import (
    ChunkIncompleteReason,
    build_python_code_chunks,
)


@dataclass(frozen=True)
class CodeGenerationCoverage:
    # 计数描述本轮支持范围；生成总数可能大于实际选中/保存的20个分块。
    scanned_directories: int
    inspected_files: int
    parsed_files: int
    examined_symbols: int
    generated_chunks: int
    definition_lines: int
    excluded_module_lines: int
    excluded_counts: dict[str, int]
    incomplete_reasons: tuple[ChunkIncompleteReason, ...]


@dataclass(frozen=True)
class GeneratedCodeEmbeddingBatch:
    workspace_id: str
    task_id: str
    batch: SavedCodeEmbeddingBatch
    requested_model: str
    response_model: str
    request_count: int
    # 用量只描述本次生成；供应商未报告仍为None，不能伪装成零。
    prompt_tokens: int | None
    total_tokens: int | None
    coverage: CodeGenerationCoverage
    source: str = "authorized_code_batch_generation"


def _check_cancellation() -> None:
    # 防止受控/未来客户端吞掉取消后，把迟到响应保存成成功批次。
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()


async def generate_and_save_code_batch(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    config: EmbeddingConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeneratedCodeEmbeddingBatch:
    """仅可信宿主调用；目标来自当前执行范围，配置/transport不可由模型指定。"""
    _check_cancellation()
    # 配置关闭/无效时不读取源码；加载延迟配置，不借用聊天模型或密钥。
    active = config if config is not None else load_embedding_config()
    target = capture_code_embedding_target(
        user_id=user_id, workspace_id=workspace_id, task_id=task_id
    )
    # 捕获事务已退出。每次路径授权都核对同一个根和修订，然后关闭Session再读盘。
    chunks = build_python_code_chunks(
        user_id=user_id,
        workspace_id=workspace_id,
        task_id=task_id,
        expected_bound_root=target.bound_root,
        expected_binding_revision=target.binding_revision,
    )
    if not chunks.chunks:
        raise CodeVectorStorageError("code_embedding_chunks_empty")

    def check_target() -> None:
        _check_cancellation()
        # 每次HTTP请求前开启/结束短事务；旧捕获值或分块本身不是发送许可。
        current = capture_code_embedding_target(
            user_id=user_id, workspace_id=workspace_id, task_id=task_id
        )
        if current != target:
            raise CodeVectorStorageError("code_embedding_binding_changed")

    # 首次前置拒绝保留领域错误；之后每次请求的拒绝沿用生成器固定错误边界。
    check_target()
    generated = await generate_code_embeddings(
        chunks, config=active, transport=transport, before_request=check_target
    )
    _check_cancellation()
    # 校验生成输出确实对应本轮全部选中来源，不能入库另一份有效但不相关的快照。
    if (
        type(generated) is not CodeEmbeddings
        or generated.workspace_id != workspace_id
        or generated.task_id != task_id
        or generated.files != chunks.files
        or tuple(item.chunk for item in generated.embeddings) != chunks.chunks
        or generated.truncated != chunks.truncated
        or generated.incomplete_reasons != chunks.incomplete_reasons
        or generated.chunk_strategy != chunks.strategy
        or generated.chunk_policy != chunks.policy
        or generated.chunk_parser != chunks.parser
        or generated.response_model is None
    ):
        raise CodeVectorStorageError("code_embedding_generation_result_invalid")
    # 保存再次锁定当前归属/绑定；空间、批次和全部向量同一短事务成功commit才返回。
    # 模型等待、文件I/O不占此事务；失败不覆盖旧批次，也不自动重放已付费请求。
    saved = save_code_embedding_batch(target=target, source=generated, config=active)
    return GeneratedCodeEmbeddingBatch(
        workspace_id=workspace_id,
        task_id=task_id,
        batch=saved,
        requested_model=generated.requested_model,
        response_model=generated.response_model,
        request_count=generated.request_count,
        prompt_tokens=generated.prompt_tokens,
        total_tokens=generated.total_tokens,
        coverage=CodeGenerationCoverage(
            scanned_directories=chunks.scanned_directories,
            inspected_files=chunks.inspected_files,
            parsed_files=chunks.parsed_files,
            examined_symbols=chunks.examined_symbols,
            generated_chunks=chunks.generated_chunks,
            definition_lines=chunks.definition_lines,
            excluded_module_lines=chunks.excluded_module_lines,
            excluded_counts=dict(chunks.excluded_counts),
            incomplete_reasons=chunks.incomplete_reasons,
        ),
    )
