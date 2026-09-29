"""本地变更组查询与显式决策/应用/恢复；所有身份来自服务端。"""
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.parameters import ProposalDecisionIdentifier
from app.routers.workspace.http import _error_response
from app.services.workspace.proposals.change_sets import list_change_sets, decide_change_set, execute_change_set

router = APIRouter(prefix='/workspaces', tags=['workspaces'], route_class=WorkspaceRoute)
BASE = '/{workspace_id}/tasks/{task_id}/change-sets'

class ChangeAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['approve', 'reject', 'apply', 'restore']

@router.get(BASE)
def list_changes(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
                 request: Request, current_user: CurrentUser):
    if request.query_params:
        return _error_response(422, code='invalid_workspace_input', message='此查询不接受额外参数')
    return {'workspace_id': workspace_id, 'task_id': task_id,
            'items': list_change_sets(user_id=current_user.id, workspace_id=workspace_id, task_id=task_id)}

@router.post(BASE + '/{change_id}')
def change_action(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier,
                  change_id: ProposalDecisionIdentifier, payload: ChangeAction, request: Request, current_user: CurrentUser):
    if request.query_params:
        return _error_response(422, code='invalid_workspace_input', message='此操作不接受额外参数')
    scope = {'user_id': current_user.id, 'workspace_id': workspace_id, 'task_id': task_id, 'change_id': change_id}
    try:
        item = (decide_change_set(**scope, action=payload.action) if payload.action in {'approve', 'reject'}
                else execute_change_set(**scope, restore=payload.action == 'restore'))
    except ValueError:
        return _error_response(409, code='change_set_conflict', message='变更条件不再满足，请查询状态；未确认的执行不要重放')
    return {'workspace_id': workspace_id, 'task_id': task_id, 'item': item}
