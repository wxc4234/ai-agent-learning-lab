"""显式本地代码上下文查询；服务端选择模型连接、Top K和构建预算。"""

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.dependencies import CurrentUser
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.routers.workspace.parameters import TaskIdentifier
from app.schemas import WorkspaceErrorResponse
from app.services.model.embedding_config import (
    EmbeddingConfig,
    EmbeddingError,
    load_embedding_config,
)
from app.services.model.query_embeddings import _prepare_query
from app.services.workspace.files.code_context import (
    CodeContextBudget,
    CodeContextError,
    Count,
    OmissionReason,
    TokenUsage,
    _Hit,
    _Metadata,
)
from app.services.workspace.files.code_query_context import (
    CodeQueryContextResult,
    build_code_query_context,
)
from app.services.workspace.files.code_vector_search import CodeVectorSearchError
from app.services.workspace.files.code_vector_storage import Digest, _Record


router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute
)


class CodeQueryContextRequest(BaseModel):
    # 只接受显式查询及批次空间选择；身份、URL、Key、预算与Top K不能由正文覆盖。
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    query: str = Field(min_length=1, max_length=2000)
    batch_id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    response_model: str = Field(min_length=1, max_length=256)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        _prepare_query(value)  # 检查UTF-8字节、空白、空字符；保留实际发送文本。
        return value

    @field_validator("response_model")
    @classmethod
    def validate_response_model(cls, value: str) -> str:
        return EmbeddingConfig.validate_model(value)


class _OmissionResponse(_Record):
    source_rank: int = Field(ge=1, le=20)
    chunk_id: Digest
    reasons: tuple[OmissionReason, ...] = Field(min_length=1, max_length=4)


class _RecallResponse(_Record):
    batch_chunk_count: int = Field(ge=1, le=20)
    searchable_chunk_count: Count
    excluded_zero_chunk_count: Count
    omitted_by_top_k: Count


class _ContextResponse(_Record):
    # 复用已校验来源/片段契约；额外内部字段不能被无意序列化到HTTP。
    batch_id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    space_id: Digest
    dimensions: int = Field(ge=1, le=4096)
    input_hit_count: Count
    selected_hit_count: Count
    context_text: str = Field(max_length=80_000)
    context_chars: int = Field(ge=1, le=80_000)
    context_bytes: int = Field(ge=1, le=160_000)
    budget: CodeContextBudget
    selected_chunks: tuple[_Hit, ...] = Field(max_length=20)
    omissions: tuple[_OmissionResponse, ...] = Field(max_length=20)
    source_metadata: _Metadata
    recall_summary: _RecallResponse
    source: Literal["bounded_code_context"]
    content_trust: Literal["untrusted_project_content"]


class CodeQueryContextResponse(_Record):
    query_sha256: Digest
    requested_model: str
    response_model: str
    prompt_tokens: TokenUsage | None
    total_tokens: TokenUsage | None
    request_count: Literal[1]
    context: _ContextResponse
    query_source: Literal["query_embedding"]
    source: Literal["code_query_context"]


# 只映射已知固定码；未知业务码也走通用失败，不反射error.code或str(error)。
_ERRORS = {
    "embedding_not_configured": (503, "请先配置独立Embedding地址、模型、密钥与维度"),
    "embedding_config_invalid": (503, "Embedding配置无效"),
    "embedding_query_invalid": (422, "查询文本不符合要求"),
    "embedding_query_budget_exceeded": (422, "查询文本超过字符或字节预算"),
    "embedding_timeout": (504, "Embedding请求超时，未返回代码上下文"),
    "embedding_request_failed": (502, "Embedding请求失败，未返回代码上下文"),
    "embedding_response_invalid": (502, "Embedding响应无效，未返回代码上下文"),
    "embedding_response_too_large": (502, "Embedding响应超过读取预算"),
    "code_embedding_query_invalid": (422, "查询或模型版本不符合要求"),
    "code_embedding_query_zero": (502, "查询向量无法用于余弦召回"),
    "code_embedding_query_result_invalid": (409, "查询结果与指定模型空间不一致"),
    "code_embedding_batch_inconsistent": (409, "指定批次来源不一致"),
    "code_embedding_distance_invalid": (500, "代码召回距离无效"),
    "code_context_budget_invalid": (500, "服务端上下文预算无效"),
    "code_context_budget_too_small": (409, "当前预算无法容纳批次来源声明"),
    "code_context_query_result_invalid": (500, "查询上下文来源不一致"),
    "code_context_snapshot_invalid": (500, "代码上下文快照无效"),
    "code_context_snapshot_too_large": (500, "代码上下文快照超过构建预算"),
}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _public_response(
    result: CodeQueryContextResult,
    *,
    body: CodeQueryContextRequest,
    workspace_id: str,
    task_id: str,
    config: EmbeddingConfig,
) -> dict:
    if type(result) is not CodeQueryContextResult:
        raise ValueError("unknown_code_context_result")
    payload = asdict(result)
    # dataclasses.asdict不转换Pydantic预算；这里只投影三个已知字段。
    budget = result.context.budget
    payload["context"]["budget"] = {
        "max_chunks": budget.max_chunks,
        "max_chars": budget.max_chars,
        "max_bytes": budget.max_bytes,
    }
    public = CodeQueryContextResponse.model_validate_json(
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    )
    if (
        public.query_sha256 != sha256(body.query.encode("utf-8")).hexdigest()
        or public.requested_model != config.model
        or public.response_model != body.response_model
        or public.context.batch_id != body.batch_id
        or public.context.source_metadata.workspace_id != workspace_id
        or public.context.source_metadata.task_id != task_id
    ):
        raise ValueError("mismatched_code_context_result")
    return public.model_dump(mode="json")


@router.post(
    "/{workspace_id}/tasks/{task_id}/code-query-context",
    response_model=CodeQueryContextResponse,
    responses={
        status: {"model": WorkspaceErrorResponse}
        for status in (401, 403, 404, 409, 415, 422, 500, 502, 503, 504)
    },
)
async def query_context(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    body: CodeQueryContextRequest,
    request: Request,
    current_user: CurrentUser,
) -> Response:
    if request.query_params:
        return _error_response(
            422,
            code="invalid_code_query_context_input",
            message="代码上下文查询不接受查询参数",
        )
    try:
        # FastAPI已完成类型校验；再拒绝JSON重复键，避免不同解析器选中不同范围。
        json.loads(await request.body(), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError):
        return _error_response(
            422,
            code="invalid_code_query_context_input",
            message="代码上下文查询正文无效",
        )

    try:
        config = load_embedding_config()
        # 路由不增加Session或外层事务。服务自行关闭身份预检/召回事务，
        # 模型等待不持有连接；构建及公开投影失败时整次拒绝，不重试或返回旧包。
        result = await build_code_query_context(
            body.query,
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
            batch_id=body.batch_id,
            config=config,
            response_model=body.response_model,
            top_k=5,
            budget=CodeContextBudget(),
        )
        return JSONResponse(
            _public_response(
                result,
                body=body,
                workspace_id=workspace_id,
                task_id=task_id,
                config=config,
            ),
            headers={"Cache-Control": "no-store"},
        )
    except WorkspaceNotAccessibleError:
        raise  # 统一404不区分不存在、无权或旧绑定；由WorkspaceRoute脱敏。
    except (EmbeddingError, CodeVectorSearchError, CodeContextError) as error:
        if error.code in _ERRORS:
            status, message = _ERRORS[error.code]
            return _error_response(status, code=error.code, message=message)
    except Exception:  # noqa: BLE001 -- 不反射供应商体、Key、宿主路径或未知异常
        return _error_response(
            500,
            code="code_query_context_failed",
            message="代码上下文查询失败，结果未知",
        )
    return _error_response(
        500, code="code_query_context_failed", message="代码上下文查询失败，结果未知"
    )
