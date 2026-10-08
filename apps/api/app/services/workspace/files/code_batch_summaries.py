"""当前任务/绑定下的批次摘要；列表是快照，不是生成、读取或发送许可。"""

import json
import re
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.database import SessionLocal
from app.repositories.workspace.code_embedding_repository import (
    CODE_BATCH_METADATA_BYTES,
    CODE_BATCH_SUMMARY_LIMIT,
    read_owned_code_embedding_batch_summaries,
)
from app.repositories.workspace.file_edit_proposal_repository import lock_owned_proposal_task
from app.services.workspace.files.code_context import _Metadata
from app.services.workspace.files.code_vector_storage import Digest, _Record
from app.services.workspace.files.python_chunks import ChunkIncompleteReason


class CodeBatchSummaryError(ValueError):
    """固定分类不包含原始元数据、目录、SQL或配置。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CodeEmbeddingBatchSummary(_Record):
    batch_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    space_id: Digest
    requested_model: str = Field(min_length=1, max_length=256)
    response_model: str = Field(min_length=1, max_length=256)
    dimensions: int = Field(ge=1, le=4096)
    chunk_count: int = Field(ge=1, le=20)
    # 原覆盖声明只描述生成时快照，不是整个项目或当前磁盘覆盖。
    truncated: bool
    incomplete_reasons: tuple[ChunkIncompleteReason, ...] = Field(max_length=6)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def consistency(self) -> "CodeEmbeddingBatchSummary":
        if (
            self.truncated != bool(self.incomplete_reasons)
            or len(set(self.incomplete_reasons)) != len(self.incomplete_reasons)
        ):
            raise ValueError("invalid_batch_coverage")
        for value in (self.requested_model, self.response_model):
            if (
                value != value.strip()
                or len(value.encode("utf-8")) > 1024
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError("invalid_batch_model")
        return self


class CodeEmbeddingBatchList(_Record):
    workspace_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    task_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    batches: tuple[CodeEmbeddingBatchSummary, ...] = Field(max_length=20)
    has_more: bool
    limit: Literal[20] = 20
    source: Literal["code_embedding_batch_summaries"] = "code_embedding_batch_summaries"

    @model_validator(mode="after")
    def consistency(self) -> "CodeEmbeddingBatchList":
        order = [(item.created_at, item.batch_id) for item in self.batches]
        if (
            len({item.batch_id for item in self.batches}) != len(self.batches)
            or order != sorted(order, reverse=True)
            or (self.has_more and len(self.batches) != CODE_BATCH_SUMMARY_LIMIT)
        ):
            raise ValueError("invalid_batch_list")
        return self


def list_code_embedding_batches(
    *, user_id: int, workspace_id: str, task_id: str
) -> CodeEmbeddingBatchList:
    # 服务也可能被可信宿主直接调用；坏标识不得开启数据库事务。
    if (
        type(user_id) is not int or not 1 <= user_id <= 2**31 - 1
        or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None
            for value in (workspace_id, task_id)
        )
    ):
        raise CodeBatchSummaryError("invalid_code_embedding_batch_list_input")

    # 与保存/绑定/删除同序锁定当前归属；事务内只有有界数据库读取和元数据校验。
    # 没有模型、磁盘I/O或向量查询；成功退出事务前不发布列表。
    with SessionLocal.begin() as session:
        workspace, _ = lock_owned_proposal_task(
            session, user_id=user_id, workspace_id=workspace_id, task_id=task_id
        )
        if workspace.root_path is None:
            raise CodeBatchSummaryError("code_embedding_project_unbound")
        rows = read_owned_code_embedding_batch_summaries(
            session, user_id=user_id, workspace_id=workspace_id, task_id=task_id
        )
        try:
            if len(rows) > CODE_BATCH_SUMMARY_LIMIT + 1:
                raise ValueError("batch_list_limit")
            items = []
            for row in rows:
                if (
                    type(row["metadata_bytes"]) is not int
                    or not 1 <= row["metadata_bytes"] <= CODE_BATCH_METADATA_BYTES
                ):
                    raise ValueError("batch_metadata_limit")
                encoded = json.dumps(row["source_metadata"], ensure_ascii=False, allow_nan=False)
                if len(encoded.encode("utf-8")) > CODE_BATCH_METADATA_BYTES:
                    raise ValueError("batch_metadata_limit")
                metadata = _Metadata.model_validate_json(encoded)
                if (
                    metadata.workspace_id != workspace_id or metadata.task_id != task_id
                    or metadata.embedding_space_id != row["space_id"]
                    or metadata.dimensions != row["dimensions"]
                    or row["space_dimensions"] != row["dimensions"]
                    or metadata.requested_model != row["requested_model"]
                    or metadata.response_model != row["response_model"]
                ):
                    raise ValueError("batch_metadata_mismatch")
                items.append(CodeEmbeddingBatchSummary(
                    batch_id=row["batch_id"], space_id=row["space_id"],
                    requested_model=metadata.requested_model, response_model=metadata.response_model,
                    dimensions=row["dimensions"], chunk_count=row["chunk_count"],
                    truncated=metadata.truncated, incomplete_reasons=metadata.incomplete_reasons,
                    created_at=row["created_at"],
                ))
            # 多读的一项也须验证；不能用列表裁剪隐藏坏来源。
            checked = sorted(items, key=lambda item: (item.created_at, item.batch_id), reverse=True)
            if items != checked or len({item.batch_id for item in items}) != len(items):
                raise ValueError("batch_order_invalid")
            result = CodeEmbeddingBatchList(
                workspace_id=workspace_id, task_id=task_id,
                batches=tuple(items[:CODE_BATCH_SUMMARY_LIMIT]),
                has_more=len(items) > CODE_BATCH_SUMMARY_LIMIT,
            )
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            raise CodeBatchSummaryError("code_embedding_batch_list_failed") from None
    return result
