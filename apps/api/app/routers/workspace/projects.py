"""项目创建和列表 HTTP 入口。"""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.database import SessionLocal
from app.dependencies import CurrentUser
from app.repositories.workspace.workspace_repository import list_owned_workspaces
from app.routers.workspace.boundary import WorkspaceRoute
from app.schemas import (
    WorkspaceCreateRequest,
    WorkspaceErrorResponse,
    WorkspaceListResponse,
    WorkspaceResponse,
)
from app.services.workspace.workspace_service import create_user_workspace

router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute
)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceResponse,
    responses={
        400: {
            "model": WorkspaceErrorResponse,
            "description": "请求无法解析",
        },
        401: {
            "model": WorkspaceErrorResponse,
            "description": "登录状态无效",
        },
        403: {
            "model": WorkspaceErrorResponse,
            "description": "请求来源不被允许",
        },
        415: {
            "model": WorkspaceErrorResponse,
            "description": "请求内容类型不支持",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "请求字段或名称不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "创建工作空间失败",
        },
    },
)
def create_workspace(
    payload: WorkspaceCreateRequest,
    current_user: CurrentUser,
) -> WorkspaceResponse:
    """创建本人工作空间；服务管理事务，路由管理 Session 生命周期。"""

    # 普通 def 路由由 FastAPI 在线程池执行，同步数据库调用不会阻塞事件循环。
    # 认证依赖已关闭自己的 Session，这里为业务创建新的独立 Session。
    with SessionLocal() as session:
        result = create_user_workspace(
            session=session,
            user_id=current_user.id,
            name=payload.name,
        )

    # 服务结果是普通数据，关闭 Session 后仍可安全组装响应。
    return WorkspaceResponse(
        external_id=result.external_id,
        name=result.name,
        created_at=result.created_at,
    )


@router.get(
    "",
    response_model=WorkspaceListResponse,
    responses={
        401: {
            "model": WorkspaceErrorResponse,
            "description": "账号模式下登录状态无效",
        },
        403: {
            "model": WorkspaceErrorResponse,
            "description": "本地访问边界拒绝请求",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "列表查询参数不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "读取工作空间列表失败",
        },
    },
)
def list_workspaces(
    current_user: CurrentUser,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="返回条数，默认 20，最多 100",
        ),
    ] = 20,
) -> WorkspaceListResponse:
    """读取当前身份的工作空间，返回关闭 Session 后仍可使用的响应。"""

    # 同步数据库操作继续使用普通 def 路由，由 FastAPI 在线程池执行。
    # 身份依赖已经关闭自己的 Session，这里创建独立的查询 Session。
    with SessionLocal() as session:
        workspaces = list_owned_workspaces(
            session=session,
            user_id=current_user.id,
            limit=limit + 1,
        )

        # 在 Session 关闭前提取公开字段，不让 ORM 对象进入响应边界。
        result = WorkspaceListResponse(
            items=[
                WorkspaceResponse(
                    external_id=workspace.external_id,
                    name=workspace.name,
                    created_at=workspace.created_at,
                )
                for workspace in workspaces[:limit]
            ],
            has_more=len(workspaces) > limit,
        )

    # 列表查询没有业务写入，无需 commit；上下文退出时释放读取事务。
    return result
