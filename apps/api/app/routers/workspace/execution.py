"""受限服务端样例提案应用 HTTP 入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.routers.workspace.parameters import ProposalDecisionIdentifier
from app.routers.workspace.proposal_execution_response import (
    build_proposal_execution_response,
)
from app.schemas import (
    FileEditProposalExecutionRequest,
    FileEditProposalExecutionResponse,
    WorkspaceErrorResponse,
)
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.services.workspace.samples.sample_proposal_execution import (
    execute_sample_proposal,
)
from app.services.workspace.samples.task_sample_binding import (
    TaskSampleBindingError,
    TaskSampleBindings,
)

router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute
)


@router.post(
    "/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}/apply",
    response_model=FileEditProposalExecutionResponse,
    responses={
        401: {
            "model": WorkspaceErrorResponse,
            "description": "本机访问凭证或身份无效",
        },
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式或请求来源不符合要求",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "资源不存在或不可访问",
        },
        409: {
            "model": WorkspaceErrorResponse,
            "description": "当前任务没有可用的服务端样例登记",
        },
        415: {
            "model": WorkspaceErrorResponse,
            "description": "请求必须使用application/json",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "路径、正文或查询参数不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "执行或响应结果未确认，不能据此重复执行",
        },
    },
)
def apply_sample_file_edit_proposal(
    workspace_id: ProposalDecisionIdentifier,
    task_id: ProposalDecisionIdentifier,
    proposal_id: ProposalDecisionIdentifier,
    payload: FileEditProposalExecutionRequest,
    request: Request,
    current_user: CurrentUser,
    bindings: Annotated[TaskSampleBindings, Depends(get_sample_bindings)],
) -> FileEditProposalExecutionResponse | JSONResponse:
    """只执行服务端登记的样例提案，不接受客户端路径或登记凭据。"""

    # 三个资源编号来自路径，身份来自CurrentUser。
    # 不允许查询参数再提供另一套资源、身份或执行选项。
    if request.query_params:
        return _error_response(
            422,
            code="invalid_proposal_execution_input",
            message="提案应用请求不接受查询参数",
        )

    # HTTP正文已经由严格Schema校验；保留显式动作检查，
    # 不将空正文或未来新增动作默认解释成应用。
    if payload.action != "apply":
        return _error_response(
            422,
            code="invalid_proposal_execution_input",
            message="提案应用请求必须明确指定apply动作",
        )

    try:
        # 普通def在线程池执行同步服务。
        # 路由不持有数据库事务；样例借用覆盖完整执行过程。
        # 必须经过样例门禁，不能直接调用原始提案执行器。
        result = execute_sample_proposal(
            bindings,
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
            proposal_id=proposal_id,
        )
    except TaskSampleBindingError:
        # 只说明当前登记不可用，不推断此前请求是否已经产生副作用。
        return _error_response(
            409,
            code="sample_execution_unavailable",
            message="当前任务没有可用的服务端样例登记，不能启动本次应用",
        )

    # 不把SampleExecutionError统一映射成“未执行”：
    # 它也可能发生在执行后检查回执的阶段。
    # 未知异常及响应生成失败由WorkspaceRoute返回安全的未确认错误。
    return build_proposal_execution_response(
        workspace_id=workspace_id,
        task_id=task_id,
        proposal_id=proposal_id,
        result=result,
    )
