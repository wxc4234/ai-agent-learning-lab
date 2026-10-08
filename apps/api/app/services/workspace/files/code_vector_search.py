"""指定向量批次的授权精确召回；不调用模型，不读取文件，不修改索引。"""

from copy import deepcopy
from dataclasses import dataclass
import math

from app.database import SessionLocal
from app.repositories.workspace.code_embedding_repository import (
    rank_batch_vectors,
    read_owned_code_embedding_batch,
)
from app.repositories.workspace.file_edit_proposal_repository import (
    lock_owned_proposal_task,
)
from app.services.model.code_embeddings import code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.workspace.files.code_vector_storage import _float32


MAX_BATCH_CHUNKS = 20
MAX_TOP_K = 20


class CodeVectorSearchError(ValueError):
    """固定错误码，不反射查询向量、源码、宿主路径或配置凭证。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CodeVectorHit:
    # rank从1开始，distance是数据库返回的余弦距离，不是置信度。
    rank: int
    distance: float
    chunk: dict


@dataclass(frozen=True)
class CodeVectorSearchResult:
    batch_id: str
    space_id: str
    dimensions: int
    top_k: int

    # 分开表达批次规模、有效候选、零向量排除和Top K选择。
    batch_chunk_count: int
    searchable_chunk_count: int
    excluded_zero_chunk_count: int
    omitted_by_top_k: int

    # 原始truncated、原因、文件摘要和策略都保留在metadata中。
    metadata: dict
    hits: tuple[CodeVectorHit, ...]


def _vector_values(
    value: tuple[float, ...],
    dimensions: int,
) -> tuple[float, ...]:
    if type(value) is not tuple or len(value) != dimensions:
        raise ValueError("invalid_vector_dimensions")

    # 与存储共用float32规则，拒绝bool、字符串、非有限值及转换溢出。
    return tuple(_float32(number) for number in value)


def _checked_distance(value: float | None) -> float:
    # 显式排除None，让类型检查器与运行时都确认后续值可转换为float。
    if value is None or type(value) not in (float, int):
        raise CodeVectorSearchError("code_embedding_distance_invalid")

    distance = float(value)
    if not math.isfinite(distance) or not 0.0 <= distance <= 2.0:
        raise CodeVectorSearchError("code_embedding_distance_invalid")

    return distance


def search_code_embedding_batch(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    batch_id: str,
    config: EmbeddingConfig,
    response_model: str,
    query_vector: tuple[float, ...],
    top_k: int = 5,
) -> CodeVectorSearchResult:
    """消费可信宿主提供的查询向量，对当前授权批次返回有界Top K。"""
    # 查询预检在事务外完成，不因明显无效参数占用归属锁。
    try:
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            raise ValueError("invalid_top_k")

        if (
            type(response_model) is not str
            or not 1 <= len(response_model) <= 256
            or response_model != response_model.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in response_model)
            or len(response_model.encode("utf-8")) > 1024
        ):
            raise ValueError("invalid_response_model")

        prepared_query = _vector_values(query_vector, config.dimensions)
        space_id = code_embedding_space_id(config, response_model)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise CodeVectorSearchError("code_embedding_query_invalid") from None

    # 检查量化后的值；极小输入可能在float32转换后全部变为零。
    if not any(number != 0.0 for number in prepared_query):
        raise CodeVectorSearchError("code_embedding_query_zero")

    with SessionLocal.begin() as session:
        # 事务边界：授权、绑定复核和距离读取共用当前归属锁。
        # 该事务内没有文件读取、模型请求或业务数据写入。
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

        metadata = deepcopy(batch.source_metadata)
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

        rows = rank_batch_vectors(
            session,
            batch=batch,
            query_vector=prepared_query,
        )

        # 距离排序改变了读取顺序，但批内原始序号仍必须完整且唯一。
        if len(rows) != batch.chunk_count or sorted(
            vector.ordinal for vector, _ in rows
        ) != list(range(batch.chunk_count)):
            raise CodeVectorSearchError("code_embedding_batch_inconsistent")

        all_hits: list[CodeVectorHit] = []
        excluded_zero = 0

        for vector, raw_distance in rows:
            try:
                stored_vector = _vector_values(
                    tuple(vector.embedding),
                    batch.dimensions,
                )
            except (ValueError, TypeError, UnicodeError, OverflowError):
                raise CodeVectorSearchError(
                    "code_embedding_batch_inconsistent"
                ) from None

            if not any(number != 0.0 for number in stored_vector):
                if raw_distance is not None:
                    raise CodeVectorSearchError("code_embedding_distance_invalid")
                excluded_zero += 1
                continue

            # 有限输入也不保证计算结果有效；未知距离不能当作低相关度。
            distance = _checked_distance(raw_distance)

            if (
                type(vector.chunk_metadata) is not dict
                or vector.chunk_metadata.get("chunk_id") != vector.chunk_id
            ):
                raise CodeVectorSearchError("code_embedding_batch_inconsistent")

            # 保留原始相对路径、摘要、符号、半开坐标及分片信息。
            # 复制后补回正文，不修改ORM行中的JSON对象。
            chunk = deepcopy(vector.chunk_metadata)
            chunk["text"] = vector.content

            all_hits.append(
                CodeVectorHit(
                    rank=len(all_hits) + 1,
                    distance=distance,
                    chunk=chunk,
                )
            )

        # 全部有界候选通过校验后才截取Top K，异常不返回部分成功。
        selected = tuple(all_hits[:top_k])
        result = CodeVectorSearchResult(
            batch_id=batch.external_id,
            space_id=batch.space_id,
            dimensions=batch.dimensions,
            top_k=top_k,
            batch_chunk_count=batch.chunk_count,
            searchable_chunk_count=len(all_hits),
            excluded_zero_chunk_count=excluded_zero,
            omitted_by_top_k=len(all_hits) - len(selected),
            metadata=metadata,
            hits=selected,
        )

    # 返回普通快照，不把依赖会话生命周期的ORM对象带出事务。
    return result
