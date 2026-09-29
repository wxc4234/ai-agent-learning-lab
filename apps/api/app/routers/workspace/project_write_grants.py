"""本地显式许可管理与用户发起的文件应用入口。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.routers.workspace.parameters import ProposalDecisionIdentifier
from app.schemas import (
    ProjectWriteAssessmentRequest, ProjectWriteAssessmentResponse,
    ProjectWriteGrantIssueRequest, ProjectWriteGrantRevokeRequest,
    ProjectWriteGrantPublicRecord, ProjectWriteGrantResponse, WorkspaceErrorResponse,
)
from app.services.workspace.proposals.project_write_grants import (
    ProjectWriteGrantService, ProjectWriteGrantConflictError,
)
from app.services.workspace.proposals.project_write_policy import ProjectWriteGrant, ProjectWriteDecision

router = APIRouter(
    prefix='/workspaces', tags=['workspaces'], route_class=WorkspaceRoute,
    responses={status: {'model': WorkspaceErrorResponse} for status in (401, 403, 404, 409, 415, 422, 500)},
)
BASE = '/{workspace_id}/tasks/{task_id}/file-edit-proposals/{proposal_id}/write-grant'


@router.get(BASE + '/audit')
def read_audit(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
               proposal_id: ProposalDecisionIdentifier, request: Request, current_user: CurrentUser):
    from app.services.workspace.proposals.proposal_recovery import read_proposal_audit
    if request.query_params:
        return _error_response(422, code='invalid_project_write_grant_input', message='审计请求不接受查询参数')
    events = read_proposal_audit(user_id=current_user.id, workspace_id=workspace_id,
                                 task_id=task_id, proposal_id=proposal_id)
    return {'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id, 'events': events}


class RestoreProposalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['restore']


@router.post(BASE + '/restore-proposal')
def restore_proposal(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
                     proposal_id: ProposalDecisionIdentifier, payload: RestoreProposalRequest,
                     request: Request, current_user: CurrentUser):
    from app.services.workspace.proposals.proposal_recovery import create_restore_proposal
    if request.query_params:
        return _error_response(422, code='invalid_project_write_grant_input', message='恢复请求不接受查询参数')
    try:
        restored = create_restore_proposal(user_id=current_user.id, workspace_id=workspace_id,
                                           task_id=task_id, proposal_id=proposal_id)
    except ProjectWriteGrantConflictError:
        return _error_response(409, code='project_write_grant_conflict', message='原应用未确认或文件已变化，不能生成恢复提案')
    return {'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id, 'restore_proposal_id': restored}


def _host(request: Request) -> ProjectWriteGrantService:
    host = getattr(request.app.state, 'project_write_grants', None)
    if not isinstance(host, ProjectWriteGrantService):
        # 生命周期缺失时失败，不按请求新建宿主或复用其他应用实例。
        raise TypeError('project_write_host_unavailable')
    return host


def _response(workspace_id: str, task_id: str, proposal_id: str,
              grant: ProjectWriteGrant | None) -> ProjectWriteGrantResponse:
    public = None
    if grant is not None:
        grant = ProjectWriteGrant.model_validate(grant)
        if (grant.enabled, grant.revision) not in ((True, 1), (False, 2)):
            raise ValueError('invalid_grant_state')
        # 显式重建白名单；内部target含宿主/资源/对象身份，不能直接序列化。
        public = ProjectWriteGrantPublicRecord.model_validate({
            'grant_id': grant.grant_id, 'revision': grant.revision,
            'status': 'enabled' if grant.enabled else 'revoked',
        })
    return ProjectWriteGrantResponse(workspace_id=workspace_id, task_id=task_id, proposal_id=proposal_id, grant=public)


@router.get(BASE, response_model=ProjectWriteGrantResponse)
def read_grant(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
               proposal_id: ProposalDecisionIdentifier, request: Request, current_user: CurrentUser) -> ProjectWriteGrantResponse:
    grant = _host(request).read(user_id=current_user.id, workspace_id=workspace_id, task_id=task_id, proposal_id=proposal_id)
    return _response(workspace_id, task_id, proposal_id, grant)


@router.post(BASE, response_model=ProjectWriteGrantResponse, status_code=201)
def issue_grant(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
                proposal_id: ProposalDecisionIdentifier, payload: ProjectWriteGrantIssueRequest,
                request: Request, current_user: CurrentUser) -> ProjectWriteGrantResponse | JSONResponse:
    # 同步服务在线程池执行，事务完全由服务持有，不在HTTP层重试。
    try:
        grant = _host(request).issue(user_id=current_user.id, workspace_id=workspace_id, task_id=task_id, proposal_id=proposal_id)
    except ProjectWriteGrantConflictError:
        return _error_response(409, code='project_write_grant_conflict', message='该提案已有许可记录，请查询当前状态')
    if grant is None or not grant.enabled or grant.revision != 1:
        raise ValueError('invalid_issue_result')
    return _response(workspace_id, task_id, proposal_id, grant)


@router.post(BASE + '/revoke', response_model=ProjectWriteGrantResponse)
def revoke_grant(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
                 proposal_id: ProposalDecisionIdentifier, payload: ProjectWriteGrantRevokeRequest,
                 request: Request, current_user: CurrentUser) -> ProjectWriteGrantResponse | JSONResponse:
    try:
        grant = _host(request).revoke(user_id=current_user.id, workspace_id=workspace_id, task_id=task_id,
                                      proposal_id=proposal_id, grant_id=payload.grant_id, revision=payload.revision)
    except ProjectWriteGrantConflictError:
        return _error_response(409, code='project_write_grant_conflict', message='许可不存在、已撤销或修订不匹配，请重新查询')
    if grant is None or grant.enabled or grant.revision != 2 or grant.grant_id != payload.grant_id:
        raise ValueError('invalid_revoke_result')
    return _response(workspace_id, task_id, proposal_id, grant)


@router.post(BASE + '/assessment', response_model=ProjectWriteAssessmentResponse)
def assess_grant(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
                 proposal_id: ProposalDecisionIdentifier, payload: ProjectWriteAssessmentRequest,
                 request: Request, current_user: CurrentUser) -> ProjectWriteAssessmentResponse:
    # 宿主持有短读事务与可信文件观察；HTTP层不领取应用机会，也不重试。
    decision = _host(request).assess(
        user_id=current_user.id, workspace_id=workspace_id, task_id=task_id,
        proposal_id=proposal_id, grant_id=payload.grant_id, revision=payload.revision,
        apply_requested=payload.apply_requested,
    )
    if not isinstance(decision, ProjectWriteDecision):
        raise TypeError('invalid_assessment_result')
    # 白名单区分观察结果；eligible不是执行回执，也不授予占用。
    return ProjectWriteAssessmentResponse.model_validate({
        'workspace_id': workspace_id, 'task_id': task_id, 'proposal_id': proposal_id,
        'result': decision.code,
    })


@router.post(BASE + '/apply')
def apply_grant(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
                proposal_id: ProposalDecisionIdentifier, payload: ProjectWriteGrantRevokeRequest,
                request: Request, current_user: CurrentUser):
    """重新授权后执行已批准提案；不接受客户端目标路径和候选内容。"""
    from app.routers.workspace.proposal_execution_response import build_proposal_execution_response
    if request.query_params:
        return _error_response(422, code='invalid_project_write_grant_input', message='应用请求不接受查询参数')
    result = _host(request).apply(
        user_id=current_user.id, workspace_id=workspace_id, task_id=task_id,
        proposal_id=proposal_id, grant_id=payload.grant_id, revision=payload.revision,
    )
    return build_proposal_execution_response(
        workspace_id=workspace_id, task_id=task_id, proposal_id=proposal_id, result=result,
    )
