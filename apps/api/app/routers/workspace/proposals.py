"""提案审阅、审批与应用状态查询 HTTP 入口。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.routers.workspace.parameters import ProposalDecisionIdentifier, TaskIdentifier
from app.schemas import (
    FileEditProposalApplicationStatusResponse,
    FileEditProposalDecisionRequest,
    FileEditProposalDecisionResponse,
    FileEditProposalDetailResponse,
    WorkspaceErrorResponse,
)
from app.services.workspace.proposals.file_edit_proposal_application_query import (
    get_task_file_edit_proposal_application_status,
)
from app.services.workspace.proposals.file_edit_proposal_decision import (
    ProposalDecisionError,
    decide_task_file_edit_proposal,
)
from app.services.workspace.proposals.file_edit_proposal_service import (
    ProposalBindingChangedError,
    get_task_file_edit_proposal,
)

router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute
)


@router.get(
    "/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}",
    response_model=FileEditProposalDetailResponse,
    responses={
        401: {
            "model": WorkspaceErrorResponse,
            "description": "身份无效",
        },
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式或本地访问边界校验失败",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "项目、任务或提案不存在或不可访问",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "公开标识格式不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "提案详情暂时无法读取",
        },
    },
)
def read_file_edit_proposal_detail(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    proposal_id: TaskIdentifier,
    current_user: CurrentUser,
) -> FileEditProposalDetailResponse:
    """返回已授权提案的保存快照，不检查当前文件是否仍符合基线。"""

    # 身份由服务端依赖解析，不能从请求参数接受user_id。
    # 使用普通def，让同步数据库查询在线程池中执行。
    detail = get_task_file_edit_proposal(
        user_id=current_user.id,
        workspace_id=workspace_id,
        task_id=task_id,
        proposal_id=proposal_id,
    )

    # 查询服务已经关闭Session；这里只转换公开普通字段，
    # 不开启新事务、不返回ORM对象，也不重新读取文件。
    # 服务状态为str；在HTTP边界验证Literal，而非强转或伪造pending。
    return FileEditProposalDetailResponse.model_validate(
        {
            "proposal_id": detail.proposal_id,
            "workspace_id": detail.workspace_id,
            "task_id": detail.task_id,
            "relative_path": detail.relative_path,
            "status": detail.status,
            "baseline_sha256": detail.baseline_sha256,
            "proposed_sha256": detail.proposed_sha256,
            "diff": detail.diff,
            "diff_truncated": detail.diff_truncated,
            "created_at": detail.created_at,
        }
    )


@router.post(
    "/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}/decision",
    response_model=FileEditProposalDecisionResponse,
    responses={
        401: {
            "model": WorkspaceErrorResponse,
            "description": "身份无效",
        },
        403: {
            "model": WorkspaceErrorResponse,
            "description": "本地访问边界或请求来源不符合要求",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "项目、任务或提案不存在或不可访问",
        },
        409: {
            "model": WorkspaceErrorResponse,
            "description": "提案状态、目录绑定或Diff完整性不允许此决策",
        },
        415: {
            "model": WorkspaceErrorResponse,
            "description": "请求必须使用application/json",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "审批请求参数不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "决策结果未确认，应先查询详情",
        },
    },
)
def decide_file_edit_proposal(
    workspace_id: ProposalDecisionIdentifier,
    task_id: ProposalDecisionIdentifier,
    proposal_id: ProposalDecisionIdentifier,
    payload: FileEditProposalDecisionRequest,
    request: Request,
    current_user: CurrentUser,
) -> FileEditProposalDecisionResponse | JSONResponse:
    """记录可信用户的批准或拒绝，不执行文件写入。"""

    # 资源来自路径，决定来自正文，身份来自依赖。
    # 不接受另一套查询参数，避免出现互相矛盾的资源或身份来源。
    if request.query_params:
        return _error_response(
            422,
            code="invalid_proposal_decision_input",
            message="提案审批请求不接受查询参数",
        )

    try:
        # 普通def在线程池执行同步数据库服务。
        # 事务由服务独占；路由不另开事务，也不自动重试。
        result = decide_task_file_edit_proposal(
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
            proposal_id=proposal_id,
            decision=payload.decision,
        )
    except ProposalBindingChangedError:
        return _error_response(
            409,
            code="proposal_binding_changed",
            message="任务或项目目录绑定已变化，请重新生成提案",
        )
    except ProposalDecisionError as error:
        # 只映射已知错误码，不向客户端输出异常字符串。
        decision_errors = {
            "proposal_decision_invalid": (
                422,
                "提案决策只能是批准或拒绝",
            ),
            "proposal_state_conflict": (
                409,
                "提案已不处于待审批状态，请重新读取详情",
            ),
            "proposal_diff_incomplete": (
                409,
                "提案Diff已截断，不能直接批准",
            ),
        }
        mapped_error = decision_errors.get(error.code)

        if mapped_error is None:
            # 未知业务错误交给WorkspaceRoute按结果未确认处理。
            raise

        status_code, message = mapped_error
        return _error_response(
            status_code,
            code=error.code,
            message=message,
        )

    # 服务返回前已提交成功；只复制公开字段。
    # 在HTTP边界验证真实状态，不能硬编码为请求中的decision。
    return FileEditProposalDecisionResponse.model_validate(
        {
            "proposal_id": result.proposal_id,
            "workspace_id": result.workspace_id,
            "task_id": result.task_id,
            "status": result.status,
        }
    )


@router.get(
    "/{workspace_id}/tasks/{task_id}"
    "/file-edit-proposals/{proposal_id}/application-status",
    response_model=FileEditProposalApplicationStatusResponse,
    responses={
        401: {
            "model": WorkspaceErrorResponse,
            "description": "身份无效",
        },
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式或本地访问边界校验失败",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "项目、任务或提案不存在或不可访问",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "公开标识非法或携带了查询参数",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "应用状态暂时无法读取",
        },
    },
)
def read_file_edit_proposal_application_status(
    workspace_id: ProposalDecisionIdentifier,
    task_id: ProposalDecisionIdentifier,
    proposal_id: ProposalDecisionIdentifier,
    request: Request,
    current_user: CurrentUser,
) -> FileEditProposalApplicationStatusResponse | JSONResponse:
    """返回应用记录快照，不执行文件操作或状态恢复。"""

    # 复用现有严格标识类型：32位小写十六进制。
    # 查询只接受路径中的资源标识，不接受另一套身份或资源参数。
    if request.query_params:
        return _error_response(
            422,
            code="invalid_proposal_application_status_input",
            message="提案应用状态查询不接受查询参数",
        )

    # 普通def在线程池执行同步数据库查询。
    # 身份来自可信依赖；查询服务负责创建和关闭只读Session。
    result = get_task_file_edit_proposal_application_status(
        user_id=current_user.id,
        workspace_id=workspace_id,
        task_id=task_id,
        proposal_id=proposal_id,
    )

    # 只复制四个公开字段，并在HTTP边界再次验证响应协议。
    # 校验或序列化失败交给WorkspaceRoute返回安全500，不伪造状态。
    return FileEditProposalApplicationStatusResponse.model_validate(
        {
            "proposal_id": result.proposal_id,
            "workspace_id": result.workspace_id,
            "task_id": result.task_id,
            "application_status": result.application_status,
        }
    )
