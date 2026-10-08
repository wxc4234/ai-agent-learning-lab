"""本地批次摘要查询；只提供显式选择所需字段，不装配生成或模型请求。"""

from typing import Annotated

from fastapi import APIRouter, Path, Response
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.schemas import WorkspaceErrorResponse
from app.services.workspace.files.code_batch_summaries import (
    CodeBatchSummaryError,
    CodeEmbeddingBatchList,
    list_code_embedding_batches,
)


router = APIRouter(prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute)
ScopeIdentifier = Annotated[str, Path(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")]


@router.get(
    "/{workspace_id}/tasks/{task_id}/code-embedding-batches",
    response_model=CodeEmbeddingBatchList,
    responses={status: {"model": WorkspaceErrorResponse} for status in (401, 403, 404, 409, 422, 500)},
)
def code_batches(
    workspace_id: ScopeIdentifier,
    task_id: ScopeIdentifier,
    current_user: CurrentUser,
) -> Response:
    try:
        result = list_code_embedding_batches(
            user_id=current_user.id, workspace_id=workspace_id, task_id=task_id
        )
    except CodeBatchSummaryError as error:
        if error.code == "code_embedding_project_unbound":
            return _error_response(409, code=error.code, message="请先绑定项目目录")
        if error.code == "invalid_code_embedding_batch_list_input":
            return _error_response(422, code=error.code, message="代码向量批次查询参数不符合要求")
        # 未知业务分类也是失败，不能反射任意code或退化为无批次。
        raise
    if type(result) is not CodeEmbeddingBatchList:
        raise ValueError("unknown_batch_list")
    # 重新验证防止绕过构造的实例/新增私有字段穿透；响应边界涵盖序列化失败。
    public = CodeEmbeddingBatchList.model_validate_json(result.model_dump_json())
    if public.workspace_id != workspace_id or public.task_id != task_id:
        raise ValueError("batch_list_scope_mismatch")
    return JSONResponse(public.model_dump(mode="json"), headers={"Cache-Control": "no-store"})
