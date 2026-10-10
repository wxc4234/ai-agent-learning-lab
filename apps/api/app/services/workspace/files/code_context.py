"""从单批召回快照构造有界上下文；不读取文件或授予后续发送许可。"""

from dataclasses import asdict, dataclass
import json
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.workspace.files.code_vector_search import CodeVectorSearchResult
from app.services.workspace.files.code_vector_storage import (
    Digest,
    _Chunk,
    _File,
    _Record,
)
from app.services.workspace.files.python_chunks import ChunkIncompleteReason


if TYPE_CHECKING:
    from app.services.workspace.files.code_hybrid_context import _HybridSnapshot

MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024

Count = Annotated[int, Field(ge=0, le=20)]
TokenUsage = Annotated[int, Field(ge=0, le=2**63 - 1)]
OmissionReason = Literal[
    "duplicate_chunk",
    "chunk_budget",
    "character_budget",
    "byte_budget",
]


class CodeContextError(ValueError):
    """只暴露固定错误码，不反射正文、路径或异常细节。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CodeContextBudget(BaseModel):
    # 严格整数避免True、浮点和数字字符串意外成为预算。
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    max_chunks: int = Field(default=5, ge=1, le=20)
    max_chars: int = Field(default=12_000, ge=1, le=80_000)
    max_bytes: int = Field(default=24_000, ge=1, le=160_000)


class _Metadata(_Record):
    # 复用既有来源契约；不接受额外字段进入输出快照。
    workspace_id: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=100)
    files: tuple[_File, ...] = Field(min_length=1, max_length=20)
    requested_model: str = Field(min_length=1, max_length=256)
    response_model: str = Field(min_length=1, max_length=256)
    dimensions: int = Field(ge=1, le=4096)
    embedding_space_id: Digest
    request_count: int = Field(ge=1, le=5)
    prompt_tokens: TokenUsage | None
    total_tokens: TokenUsage | None
    truncated: bool
    incomplete_reasons: tuple[ChunkIncompleteReason, ...] = Field(max_length=6)
    chunk_strategy: Literal["python_innermost_definitions_v1"]
    chunk_policy: Literal["gitignore_subset_v1"]
    chunk_parser: str = Field(min_length=1, max_length=100)
    source: Literal["python_code_embeddings"]
    content_trust: Literal["untrusted_project_content"]

    @model_validator(mode="after")
    def consistency(self) -> "_Metadata":
        paths = [file.relative_path for file in self.files]
        if (
            len(set(paths)) != len(paths)
            or self.truncated != bool(self.incomplete_reasons)
            or len(set(self.incomplete_reasons)) != len(self.incomplete_reasons)
            or (self.prompt_tokens is None) != (self.total_tokens is None)
            or (
                self.prompt_tokens is not None
                and self.total_tokens is not None
                and self.total_tokens < self.prompt_tokens
            )
        ):
            raise ValueError("invalid_source_metadata")

        for label in (
            self.requested_model,
            self.response_model,
            self.chunk_parser,
        ):
            if (
                label != label.strip()
                or len(label.encode("utf-8")) > 1024
                or any(ord(char) < 32 or ord(char) == 127 for char in label)
            ):
                raise ValueError("invalid_source_label")

        return self


class _Hit(_Record):
    rank: int = Field(ge=1, le=20)
    distance: float = Field(ge=0, le=2, allow_inf_nan=False)

    # 复用正文摘要、chunk ID、半开坐标与分片的纯校验逻辑。
    chunk: _Chunk


class _Snapshot(_Record):
    batch_id: str = Field(min_length=1, max_length=100)
    space_id: Digest
    dimensions: int = Field(ge=1, le=4096)
    top_k: int = Field(ge=1, le=20)
    batch_chunk_count: int = Field(ge=1, le=20)
    searchable_chunk_count: Count
    excluded_zero_chunk_count: Count
    omitted_by_top_k: Count
    metadata: _Metadata
    hits: tuple[_Hit, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def consistency(self) -> "_Snapshot":
        if (
            self.metadata.dimensions != self.dimensions
            or self.metadata.embedding_space_id != self.space_id
            or self.searchable_chunk_count + self.excluded_zero_chunk_count
            != self.batch_chunk_count
            or len(self.hits) != min(self.top_k, self.searchable_chunk_count)
            or self.omitted_by_top_k != self.searchable_chunk_count - len(self.hits)
        ):
            raise ValueError("invalid_recall_counts_or_space")

        files = {file.relative_path: file for file in self.metadata.files}
        seen: dict[str, _Hit] = {}
        previous_distance = -1.0

        # 先检查所有输入，再选择。预算外的坏片段也不能被隐藏。
        for expected_rank, hit in enumerate(self.hits, start=1):
            chunk = hit.chunk
            file = files.get(chunk.symbol.relative_path)

            if (
                hit.rank != expected_rank
                or hit.distance < previous_distance
                or file is None
                or file.sha256 != chunk.symbol.sha256
                or len(chunk.text.encode("utf-8")) > file.byte_count
            ):
                raise ValueError("invalid_hit_provenance_or_order")

            previous = seen.get(chunk.chunk_id)
            if previous is not None and (
                previous.chunk != chunk or previous.distance != hit.distance
            ):
                raise ValueError("conflicting_duplicate_chunk")

            seen.setdefault(chunk.chunk_id, hit)
            previous_distance = hit.distance

        return self


@dataclass(frozen=True)
class CodeContextOmission:
    # 使用原召回rank，方便回溯选择过程；不复制被省略的正文。
    source_rank: int
    chunk_id: str
    reasons: tuple[OmissionReason, ...]


@dataclass(frozen=True)
class CodeContextPackage:
    batch_id: str
    space_id: str
    dimensions: int
    input_hit_count: int
    selected_hit_count: int

    # 此文本才是本课字符/字节预算的计量对象。
    context_text: str
    context_chars: int
    context_bytes: int
    budget: CodeContextBudget

    # 审计快照与context_text分开；它们不会自动加入模型提示。
    selected_chunks: tuple[dict[str, Any], ...]
    omissions: tuple[CodeContextOmission, ...]
    source_metadata: dict[str, Any]
    recall_summary: dict[str, int]
    source: str = "bounded_code_context"
    content_trust: str = "untrusted_project_content"


def _read_snapshot(result: CodeVectorSearchResult) -> _Snapshot:
    try:
        if type(result) is not CodeVectorSearchResult:
            raise ValueError("invalid_result_type")

        # 有界输入序列化后重新校验并隔离嵌套对象。
        # 这是内容规模限制，不是独立进程的硬内存隔离。
        raw = json.dumps(
            asdict(result),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")

        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise CodeContextError("code_context_snapshot_too_large")

        # 数据库存储快照使用JSON数组；JSON模式按既有tuple契约解析。
        return _Snapshot.model_validate_json(raw)
    except CodeContextError:
        raise
    except (
        ValidationError,
        ValueError,
        TypeError,
        UnicodeError,
        OverflowError,
        RecursionError,
    ):
        raise CodeContextError("code_context_snapshot_invalid") from None


def _render(
    snapshot: "_Snapshot | _HybridSnapshot",
    chunks: list[dict[str, Any]],
) -> str:
    # 明确这是单批选择结果。原覆盖、召回省略和本轮省略分别表达。
    payload = {
        "scope": "selected_chunks_from_one_batch",
        "batch_id": snapshot.batch_id,
        "embedding_space_id": snapshot.space_id,
        "content_trust": "untrusted_project_content",
        "chunk_strategy": snapshot.metadata.chunk_strategy,
        "chunk_policy": snapshot.metadata.chunk_policy,
        "snapshot_truncated": snapshot.metadata.truncated,
        "snapshot_incomplete_reasons": snapshot.metadata.incomplete_reasons,
        "recall_omitted_by_top_k": snapshot.omitted_by_top_k,
        "builder_omitted_hits": len(snapshot.hits) - len(chunks),
        "chunks": chunks,
    }
    strategy = getattr(snapshot, "strategy", None)
    if strategy is not None:
        payload["retrieval_strategy"] = strategy
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def build_code_context(
    result: CodeVectorSearchResult,
    *,
    budget: CodeContextBudget | None = None,
) -> CodeContextPackage:
    """对宿主选定的召回快照做纯内存选择，不重建当前访问授权。"""
    active = CodeContextBudget() if budget is None else budget
    if type(active) is not CodeContextBudget:
        raise CodeContextError("code_context_budget_invalid")

    snapshot = _read_snapshot(result)
    return _build_context(snapshot, active, recall_summary={
        "batch_chunk_count": snapshot.batch_chunk_count,
        "searchable_chunk_count": snapshot.searchable_chunk_count,
        "excluded_zero_chunk_count": snapshot.excluded_zero_chunk_count,
        "omitted_by_top_k": snapshot.omitted_by_top_k,
    }, source="bounded_code_context")


def _build_context(
    snapshot: "_Snapshot | _HybridSnapshot",
    active: CodeContextBudget,
    *, recall_summary: dict[str, int], source: str,
) -> CodeContextPackage:
    """只接收已经验证的快照；两种检索共用完整片段与实际JSON预算算法。"""
    selected: list[dict[str, Any]] = []
    omissions: list[CodeContextOmission] = []
    seen: set[str] = set()

    # 即使没有片段，来源与覆盖声明也有开销；预算不足时不能删掉声明。
    context_text = _render(snapshot, selected)
    if (
        len(context_text) > active.max_chars
        or len(context_text.encode("utf-8")) > active.max_bytes
    ):
        raise CodeContextError("code_context_budget_too_small")

    # 无数据库事务边界：只消费已复制的内存快照，不打开Session或客户端。
    for hit in snapshot.hits:
        chunk_id = hit.chunk.chunk_id

        if chunk_id in seen:
            omissions.append(
                CodeContextOmission(hit.rank, chunk_id, ("duplicate_chunk",))
            )
            continue

        # 去重针对所有已遇到的候选，放不下的首次片段也不重复尝试。
        seen.add(chunk_id)

        if len(selected) >= active.max_chunks:
            omissions.append(CodeContextOmission(hit.rank, chunk_id, ("chunk_budget",)))
            continue

        candidate = hit.model_dump(mode="json")
        trial_chunks = [*selected, candidate]
        trial_text = _render(snapshot, trial_chunks)

        # 计量实际JSON，包含引用、键名、转义和覆盖字段的开销。
        reasons: list[OmissionReason] = []
        if len(trial_text) > active.max_chars:
            reasons.append("character_budget")
        if len(trial_text.encode("utf-8")) > active.max_bytes:
            reasons.append("byte_budget")

        if reasons:
            omissions.append(CodeContextOmission(hit.rank, chunk_id, tuple(reasons)))
            # 后面的完整小片段仍可能放得下，因此不能在这里break。
            continue

        selected.append(candidate)
        context_text = trial_text

    return CodeContextPackage(
        batch_id=snapshot.batch_id,
        space_id=snapshot.space_id,
        dimensions=snapshot.dimensions,
        input_hit_count=len(snapshot.hits),
        selected_hit_count=len(selected),
        context_text=context_text,
        context_chars=len(context_text),
        context_bytes=len(context_text.encode("utf-8")),
        budget=active,
        selected_chunks=tuple(selected),
        omissions=tuple(omissions),
        source_metadata=snapshot.metadata.model_dump(mode="json"),
        recall_summary=recall_summary,
        source=source,
    )
