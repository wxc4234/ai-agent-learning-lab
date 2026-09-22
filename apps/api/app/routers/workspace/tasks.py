"""任务创建、读取、删除、运行列表与标题 HTTP 入口。"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.database import SessionLocal
from app.dependencies import CurrentUser
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.parameters import DirectorySelectionRequest, TaskIdentifier
from app.schemas import (
    TaskCreateRequest,
    TaskDetailResponse,
    TaskResponse,
    TaskRunListResponse,
    WorkspaceErrorResponse,
)
from app.services.tasks.task_deletion_service import delete_workspace_task
from app.services.tasks.task_run_query import MAX_PAGE_SIZE, MAX_RUN_ID, list_task_runs
from app.services.tasks.task_service import create_workspace_task
from app.services.tasks.task_workspace import (
    summarize_title,
    task_detail,
    task_list,
    task_messages,
)

router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute
)


@router.post(
    "/{workspace_id}/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskResponse,
    responses={
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式或访问来源不被允许",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "工作空间不存在或不可访问",
        },
        409: {
            "model": WorkspaceErrorResponse,
            "description": "同请求键内容冲突，或原创建结果已删除",
        },
        415: {
            "model": WorkspaceErrorResponse,
            "description": "请求必须使用 application/json",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "标识、正文、标题或请求键不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "任务创建结果未确认",
        },
    },
)
def create_task(
    workspace_id: Annotated[
        str,
        Path(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-f]{32}$",
        ),
    ],
    payload: TaskCreateRequest,
    current_user: CurrentUser,
) -> TaskResponse:
    """创建或重放任务；服务管理事务，路由管理 Session。"""

    # 普通 def 路由在线程池运行，避免同步数据库调用阻塞事件循环。
    # 身份取自可信依赖；请求键不能替代项目、任务和会话归属检查。
    with SessionLocal() as session:
        result = create_workspace_task(
            session=session,
            user_id=current_user.id,
            workspace_id=workspace_id,
            title=payload.title,
            request_key=payload.request_key,
        )

    # 服务已经完成事务，业务 Session 关闭后只使用普通结果。
    # 新建和重放使用同一公开结构，不暴露请求记录、指纹或内部主键。
    # 此处响应生成失败也不能说明数据库未提交，沿用外层未确认错误。
    return TaskResponse(
        external_id=result.external_id,
        workspace_id=result.workspace_id,
        conversation_id=result.conversation_id,
        title=result.title,
        created_at=result.created_at,
    )


@router.get("/{workspace_id}/tasks")
def read_tasks(
    workspace_id: TaskIdentifier,
    current_user: CurrentUser,
    before: Annotated[int | None, Query(ge=1, le=2147483647)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return task_list(current_user.id, workspace_id, before, limit)


@router.get(
    "/{workspace_id}/tasks/{task_id}",
    response_model=TaskDetailResponse,
)
def read_task_detail(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    current_user: CurrentUser,
) -> TaskDetailResponse:
    """按公开标识定位任务，为 URL 刷新恢复提供可信数据。"""

    # 身份来自依赖解析，不接受客户端指定用户。
    # 普通 def 路由在线程池执行同步数据库读取。
    return task_detail(
        user_id=current_user.id,
        workspace_id=workspace_id,
        task_id=task_id,
    )


@router.get(
    "/{workspace_id}/tasks/{task_id}/runs",
    response_model=TaskRunListResponse,
    responses={
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式或本地访问边界校验失败",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "项目或任务不存在或不可访问",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "公开标识或分页参数不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "读取任务运行历史失败",
        },
    },
)
def read_task_runs(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    current_user: CurrentUser,
    before: Annotated[
        int | None,
        Query(
            ge=1,
            le=MAX_RUN_ID,
            description="仅返回运行 ID 小于该游标的记录；首页不传",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_SIZE,
            description="每页运行记录数量，默认 20，最多 50",
        ),
    ] = 20,
) -> TaskRunListResponse:
    """读取本人任务的运行历史概要，按运行 ID 倒序分页。"""

    # 身份由 CurrentUser 提供，公开标识只用于定位资源；
    # 项目和任务归属仍由查询服务检查。
    # 使用普通 def，让同步数据库读取在线程池执行。
    #
    # 查询服务自行创建并关闭 Session，业务查询不提交写入。
    # 路由不额外创建 Session，也不在服务返回后访问 ORM 对象。
    return list_task_runs(
        user_id=current_user.id,
        workspace_id=workspace_id,
        task_id=task_id,
        before=before,
        limit=limit,
    )


@router.delete(
    "/{workspace_id}/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式、内部凭证或请求来源不被允许",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "项目或任务不存在或不可访问",
        },
        409: {
            "model": WorkspaceErrorResponse,
            "description": "任务已有消息或运行记录，不能删除",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "公开标识不合法或请求携带正文",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "任务删除结果未确认",
        },
    },
)
def delete_task(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    current_user: CurrentUser,
) -> Response:
    """删除本人项目中已停止的任务及历史；路由管理 Session，服务管理事务。"""

    # 同步数据库操作由 FastAPI 在线程池执行。
    # 身份依赖已释放自己的 Session，业务使用无活动事务的独立 Session。
    with SessionLocal() as session:
        delete_workspace_task(
            session=session,
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
        )

    # 服务返回前已完成提交；路由不再次 commit，也不再查询已删除对象。
    # 204 必须没有响应正文，不能返回 JSON null 或空对象。
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{workspace_id}/tasks/{task_id}/messages")
def read_task_messages(
    workspace_id: TaskIdentifier, task_id: TaskIdentifier, current_user: CurrentUser
):
    return task_messages(current_user.id, workspace_id, task_id)


@router.post("/{workspace_id}/tasks/{task_id}/title")
async def update_task_title(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    payload: DirectorySelectionRequest,
    current_user: CurrentUser,
):
    return await summarize_title(current_user.id, workspace_id, task_id)
