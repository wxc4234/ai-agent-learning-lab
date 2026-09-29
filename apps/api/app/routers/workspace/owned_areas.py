"""隔离项目副本持有/导出入口；创建副本和导出均需用户显式操作。"""
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.parameters import ProposalDecisionIdentifier
from app.routers.workspace.http import _error_response
from app.services.workspace.areas.owned_areas import create_owned_area, export_owned_area, list_owned_areas

router = APIRouter(prefix='/workspaces', tags=['workspaces'], route_class=WorkspaceRoute)
BASE = '/{workspace_id}/tasks/{task_id}/owned-area'

class AreaAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['create', 'export']

@router.get(BASE)
def read_area(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier, request: Request, current_user: CurrentUser):
    if request.query_params:
        return _error_response(422, code='invalid_workspace_input', message='不接受查询参数')
    return {'workspace_id': workspace_id, 'task_id': task_id,
            **list_owned_areas(user_id=current_user.id, workspace_id=workspace_id, task_id=task_id)}

@router.post(BASE)
def mutate_area(workspace_id: ProposalDecisionIdentifier, task_id: ProposalDecisionIdentifier, payload: AreaAction,
                request: Request, current_user: CurrentUser):
    if request.query_params:
        return _error_response(422, code='invalid_workspace_input', message='不接受查询参数')
    try:
        scope = {'user_id': current_user.id, 'workspace_id': workspace_id, 'task_id': task_id}
        result = create_owned_area(**scope) if payload.action == 'create' else export_owned_area(**scope)
    except ValueError:
        return _error_response(409, code='owned_area_conflict', message='副本或原项目条件已变化，未自动写回；请查询状态')
    return {'workspace_id': workspace_id, 'task_id': task_id, 'area': result}
