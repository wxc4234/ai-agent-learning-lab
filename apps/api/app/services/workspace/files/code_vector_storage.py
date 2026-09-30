"""单批向量快照存储；模型/文件I/O在事务外，当前归属与绑定在写入时复核。"""

from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import pairwise
import json
import math
import struct
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.database import SessionLocal
from app.repositories.workspace.code_embedding_repository import (
    insert_code_embedding_batch,
    read_batch_vectors,
    read_owned_code_embedding_batch,
)
from app.repositories.workspace.file_edit_proposal_repository import (
    lock_owned_proposal_task,
)
from app.services.model.code_embeddings import CodeEmbeddings, code_embedding_space_id
from app.services.model.embedding_config import EmbeddingConfig
from app.services.workspace.directory.workspace_path import _parse_relative_path
from app.services.workspace.files.python_chunks import (
    ChunkIncompleteReason,
    ChunkSplitReason,
    _chunk,
)
from app.services.workspace.files.python_symbols import PythonSymbol


Positive = Annotated[int, Field(ge=1, le=2**31 - 1)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class CodeVectorStorageError(ValueError):
    """静态业务错误，不反射正文、宿主路径、Key或数据库参数。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _float32(number: float) -> float:
    if type(number) not in (float, int):
        raise ValueError("invalid_vector_number")
    try:
        converted = struct.unpack("!f", struct.pack("!f", float(number)))[0]
    except (OverflowError, struct.error):
        raise ValueError("invalid_float32") from None
    if not math.isfinite(converted):
        raise ValueError("invalid_float32")
    return converted


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class _File(_Record):
    relative_path: str = Field(min_length=1, max_length=4096)
    file_type: Literal["source"]
    language: Literal["python"]
    byte_count: int = Field(ge=0, le=64 * 1024)
    sha256: Digest

    @field_validator("relative_path")
    @classmethod
    def path(cls, value: str) -> str:
        # 保存规范相对路径；这只是格式校验，不读取文件或授予磁盘访问权。
        _parse_relative_path(value)
        if (
            any(part in {"", ".", ".."} for part in value.split("/"))
            or "\\" in value
            or len(value.encode("utf-8")) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid_relative_path")
        return value


class _Symbol(_Record):
    relative_path: str
    name: str = Field(min_length=1, max_length=1024)
    qualified_name: str = Field(min_length=1, max_length=1024)
    kind: Literal["function", "async_function", "class"]
    start_line: Positive
    definition_line: Positive
    end_line: Positive
    sha256: Digest

    @model_validator(mode="after")
    def names(self):
        if (
            not self.name.isidentifier()
            or self.qualified_name.split(".")[-1] != self.name
            or any(not part.isidentifier() for part in self.qualified_name.split("."))
            or len(self.qualified_name.encode("utf-8")) > 1024
        ):
            raise ValueError("invalid_symbol_name")
        return self


class _Chunk(_Record):
    chunk_id: Digest
    symbol: _Symbol
    text: str = Field(min_length=1, max_length=2000)
    start_line: Positive
    start_column: Positive
    end_line: Positive
    end_column: Positive
    line_count: int = Field(ge=1, le=40)
    text_sha256: Digest
    part_index: Positive
    part_count: Positive
    split_reasons: tuple[ChunkSplitReason, ...] = Field(max_length=4)

    @model_validator(mode="after")
    def provenance(self):
        raw = self.text.encode("utf-8")
        end_line = self.start_line + self.text.count("\n")
        end_column = (
            len(self.text.rsplit("\n", 1)[-1]) + 1
            if "\n" in self.text
            else self.start_column + len(self.text)
        )
        if (
            len(raw) > 4096
            or "\x00" in self.text
            or "\r" in self.text
            or self.text_sha256 != sha256(raw).hexdigest()
            or (self.end_line, self.end_column) != (end_line, end_column)
            or self.line_count
            != self.text.count("\n") + int(not self.text.endswith("\n"))
            or not self.symbol.start_line
            <= self.symbol.definition_line
            <= self.symbol.end_line
            or self.start_line < self.symbol.start_line
            or self.end_line - int(self.end_column == 1) > self.symbol.end_line
            or self.part_index > self.part_count
            or len(set(self.split_reasons)) != len(self.split_reasons)
        ):
            raise ValueError("invalid_chunk_provenance")
        expected = _chunk(
            PythonSymbol(**self.symbol.model_dump()),
            self.text,
            self.start_line,
            self.start_column,
            self.end_line,
            self.end_column,
            self.part_index,
        )
        if expected.chunk_id != self.chunk_id:
            raise ValueError("invalid_chunk_identity")
        return self


class _Embedded(_Record):
    chunk: _Chunk
    vector: tuple[float, ...] = Field(min_length=1, max_length=4096)

    @field_validator("vector", mode="before")
    @classmethod
    def finite_float32(cls, value):
        if type(value) is not tuple or not 1 <= len(value) <= 4096:
            raise ValueError("invalid_vector")
        return tuple(_float32(number) for number in value)


class _Batch(_Record):
    workspace_id: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=100)
    files: tuple[_File, ...] = Field(min_length=1, max_length=20)
    embeddings: tuple[_Embedded, ...] = Field(min_length=1, max_length=20)
    requested_model: str = Field(min_length=1, max_length=256)
    response_model: str = Field(min_length=1, max_length=256)
    dimensions: int = Field(ge=1, le=4096)
    embedding_space_id: Digest
    request_count: int = Field(ge=1, le=5)
    prompt_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None
    total_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None
    truncated: bool
    incomplete_reasons: tuple[ChunkIncompleteReason, ...] = Field(max_length=6)
    chunk_strategy: Literal["python_innermost_definitions_v1"]
    chunk_policy: Literal["gitignore_subset_v1"]
    chunk_parser: str = Field(min_length=1, max_length=100)
    source: Literal["python_code_embeddings"]
    content_trust: Literal["untrusted_project_content"]

    @field_validator("requested_model", "response_model", "chunk_parser")
    @classmethod
    def label(cls, value: str) -> str:
        if (
            value != value.strip()
            or len(value.encode("utf-8")) > 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid_model_or_parser")
        return value

    @model_validator(mode="after")
    def consistency(self):
        files = {file.relative_path: file for file in self.files}
        seen: set[str] = set()
        if (
            len(files) != len(self.files)
            or self.truncated != bool(self.incomplete_reasons)
            or len(set(self.incomplete_reasons)) != len(self.incomplete_reasons)
            or (self.prompt_tokens is None) != (self.total_tokens is None)
            or (
                self.prompt_tokens is not None
                and self.total_tokens is not None
                and self.total_tokens < self.prompt_tokens
            )
        ):
            raise ValueError("invalid_batch_metadata")
        for item in self.embeddings:
            chunk = item.chunk
            file = files.get(chunk.symbol.relative_path)
            if (
                file is None
                or file.sha256 != chunk.symbol.sha256
                or chunk.chunk_id in seen
                or len(item.vector) != self.dimensions
                or len(chunk.text.encode("utf-8")) > file.byte_count
            ):
                raise ValueError("invalid_batch_provenance")
            seen.add(chunk.chunk_id)
        # 同一文件的实际半开范围不可重叠；允许预算截断后只保留部分分片。
        ordered = sorted(
            (item.chunk for item in self.embeddings),
            key=lambda chunk: (
                chunk.symbol.relative_path,
                chunk.start_line,
                chunk.start_column,
            ),
        )
        for previous, current in pairwise(ordered):
            if previous.symbol.relative_path == current.symbol.relative_path and (
                previous.end_line,
                previous.end_column,
            ) > (current.start_line, current.start_column):
                raise ValueError("overlapping_chunk_provenance")
        definitions: dict[tuple, list[_Chunk]] = {}
        for chunk in ordered:
            key = tuple(chunk.symbol.model_dump().values())
            parts = definitions.setdefault(key, [])
            if parts and (
                parts[-1].part_index >= chunk.part_index
                or parts[-1].part_count != chunk.part_count
                or parts[-1].split_reasons != chunk.split_reasons
            ):
                raise ValueError("inconsistent_definition_parts")
            parts.append(chunk)
        # 未截断时已声明的定义分片须齐全；截断前缀仍保留真实总片数。
        if not self.truncated and any(
            len(parts) != parts[0].part_count
            or parts[0].part_index != 1
            or parts[-1].part_index != parts[0].part_count
            for parts in definitions.values()
        ):
            raise ValueError("missing_definition_parts")
        return self


@dataclass(frozen=True)
class CodeEmbeddingTarget:
    """可信调用方在读取/模型调用前捕获；快照本身不授予后续写入权。"""

    user_id: int
    workspace_id: str
    task_id: str
    bound_root: str
    binding_revision: int


@dataclass(frozen=True)
class SavedCodeEmbeddingBatch:
    batch_id: str
    space_id: str
    dimensions: int
    chunk_count: int
    truncated: bool


@dataclass(frozen=True)
class StoredCodeEmbeddingBatch:
    batch: SavedCodeEmbeddingBatch
    metadata: dict
    chunks: tuple[dict, ...]


def capture_code_embedding_target(
    *, user_id: int, workspace_id: str, task_id: str
) -> CodeEmbeddingTarget:
    # 与现有删除/绑定流程共用项目→任务→会话锁顺序；退出后不占用模型等待事务。
    with SessionLocal.begin() as session:
        workspace, _ = lock_owned_proposal_task(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        if workspace.root_path is None:
            raise CodeVectorStorageError("code_embedding_project_unbound")
        return CodeEmbeddingTarget(
            user_id,
            workspace_id,
            task_id,
            workspace.root_path,
            workspace.binding_revision,
        )


def save_code_embedding_batch(
    *,
    target: CodeEmbeddingTarget,
    source: CodeEmbeddings,
    config: EmbeddingConfig,
) -> SavedCodeEmbeddingBatch:
    """全部数值/来源校验在事务外；模型空间、批次和向量在短事务内一起提交。"""
    try:
        if (
            type(target.binding_revision) is not int
            or not 1 <= target.binding_revision <= 2**63 - 1
        ):
            raise ValueError("invalid_target_revision")
        prepared = _Batch.model_validate(asdict(source))
        if (
            (prepared.workspace_id, prepared.task_id)
            != (target.workspace_id, target.task_id)
            or prepared.requested_model != config.model
            or prepared.dimensions != config.dimensions
            or prepared.embedding_space_id
            != code_embedding_space_id(config, prepared.response_model)
        ):
            raise ValueError("space_or_scope_mismatch")
        payload = prepared.model_dump(mode="json")
        if (
            len(
                json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
            > 2 * 1024 * 1024
        ):
            raise ValueError("batch_budget_exceeded")
    except (ValidationError, ValueError, TypeError, UnicodeError, OverflowError):
        raise CodeVectorStorageError("code_embedding_batch_invalid") from None
    entries = payload.pop("embeddings")
    vectors = []
    for entry in entries:
        chunk = entry["chunk"]
        vectors.append(
            {
                "chunk_id": chunk["chunk_id"],
                "embedding": entry["vector"],
                "content": chunk.pop("text"),
                "chunk_metadata": chunk,
            }
        )
    with SessionLocal.begin() as session:
        workspace, task = lock_owned_proposal_task(
            session,
            user_id=target.user_id,
            workspace_id=target.workspace_id,
            task_id=target.task_id,
        )
        if (workspace.root_path, workspace.binding_revision) != (
            target.bound_root,
            target.binding_revision,
        ):
            raise CodeVectorStorageError("code_embedding_binding_changed")
        row = insert_code_embedding_batch(
            session,
            task_pk=task.id,
            bound_root=target.bound_root,
            binding_revision=target.binding_revision,
            space_id=prepared.embedding_space_id,
            dimensions=prepared.dimensions,
            requested_model=prepared.requested_model,
            response_model=prepared.response_model,
            metadata=payload,
            vectors=vectors,
        )
        result = SavedCodeEmbeddingBatch(
            row.external_id,
            row.space_id,
            row.dimensions,
            row.chunk_count,
            prepared.truncated,
        )
    # 必须成功退出事务（commit）才返回成功；异常不吞成部分批次成功。
    return result


def load_code_embedding_batch(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    batch_id: str,
    config: EmbeddingConfig,
    response_model: str,
) -> StoredCodeEmbeddingBatch:
    space_id = code_embedding_space_id(config, response_model)
    with SessionLocal.begin() as session:
        # 先持有当前归属锁，避免读取向量期间目录重绑定/Task删除改变范围。
        lock_owned_proposal_task(
            session, user_id=user_id, workspace_id=workspace_id, task_id=task_id
        )
        row = read_owned_code_embedding_batch(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
            batch_id=batch_id,
            space_id=space_id,
        )
        vectors = read_batch_vectors(session, row)
        if len(vectors) != row.chunk_count or [
            vector.ordinal for vector in vectors
        ] != list(range(row.chunk_count)):
            raise CodeVectorStorageError("code_embedding_batch_inconsistent")
        result = StoredCodeEmbeddingBatch(
            SavedCodeEmbeddingBatch(
                row.external_id,
                row.space_id,
                row.dimensions,
                row.chunk_count,
                row.source_metadata["truncated"],
            ),
            row.source_metadata,
            tuple(
                {
                    **vector.chunk_metadata,
                    "text": vector.content,
                    # PG文本输出采用float32最短十进制；显式还原存储精度再返回。
                    "vector": tuple(_float32(number) for number in vector.embedding),
                }
                for vector in vectors
            ),
        )
    return result
