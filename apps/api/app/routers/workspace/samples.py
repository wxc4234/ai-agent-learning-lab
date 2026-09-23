"""受限样例登记状态只读入口；不提供创建、恢复或关闭接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.parameters import TaskIdentifier
from app.schemas import (
    TaskSampleCleanupPreflightResponse,
    TaskSampleStatusResponse,
    WorkspaceErrorResponse,
)
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings

router = APIRouter(prefix='/workspaces', tags=['workspaces'], route_class=WorkspaceRoute)


@router.get(
    '/{workspace_id}/tasks/{task_id}/sample-status',
    response_model=TaskSampleStatusResponse,
    responses={
        401: {'model': WorkspaceErrorResponse, 'description': '身份无效'},
        403: {'model': WorkspaceErrorResponse, 'description': '模式或本机访问边界拒绝'},
        404: {'model': WorkspaceErrorResponse, 'description': '资源不存在或不可访问'},
        422: {'model': WorkspaceErrorResponse, 'description': '路径、查询或正文不符合要求'},
        500: {'model': WorkspaceErrorResponse, 'description': '查询或响应未成功，不能推断状态'},
    },
)
def read_task_sample_status(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    current_user: CurrentUser,
    bindings: Annotated[TaskSampleBindings, Depends(get_sample_bindings)],
) -> TaskSampleStatusResponse:
    # 同步数据库查询在线程池执行；事务由服务管理，不跨响应生成。
    snapshot = bindings.read_status(
        user_id=current_user.id, workspace_id=workspace_id, task_id=task_id,
    )
    # 显式投影并严格校验内部结果，未知状态不得猜成missing或ready。
    return TaskSampleStatusResponse(
        workspace_id=workspace_id, task_id=task_id,
        status=snapshot.status, sealed_reason=snapshot.sealed_reason,
    )


@router.get(
    '/{workspace_id}/tasks/{task_id}/sample-cleanup-preflight',
    response_model=TaskSampleCleanupPreflightResponse,
    responses={
        401: {'model': WorkspaceErrorResponse, 'description': '身份无效'},
        403: {'model': WorkspaceErrorResponse, 'description': '模式或本机访问边界拒绝'},
        404: {'model': WorkspaceErrorResponse, 'description': '资源不存在或不可访问'},
        422: {'model': WorkspaceErrorResponse, 'description': '路径、查询或正文不符合要求'},
        500: {'model': WorkspaceErrorResponse, 'description': '诊断读取失败，不能推断目录状态'},
    },
)
def read_task_sample_cleanup_preflight(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    current_user: CurrentUser,
    bindings: Annotated[TaskSampleBindings, Depends(get_sample_bindings)],
) -> TaskSampleCleanupPreflightResponse:
    # 同步只读服务在线程池执行；归属授权与短事务由服务管理。
    snapshot = bindings.read_cleanup_preflight(
        user_id=current_user.id, workspace_id=workspace_id, task_id=task_id,
    )
    # 只投影固定分类与资源定位符，内部异常和非法结果由统一边界脱敏。
    return TaskSampleCleanupPreflightResponse(
        workspace_id=workspace_id, task_id=task_id, result=snapshot.result,
    )
