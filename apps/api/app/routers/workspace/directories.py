"""目录绑定、状态读取和本机选择窗口 HTTP 入口。"""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from app.database import SessionLocal
from app.dependencies import CurrentUser
from app.repositories.workspace.workspace_repository import require_owned_workspace
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.parameters import DirectorySelectionRequest
from app.schemas import (
    WorkspaceDirectoryRequest,
    WorkspaceDirectoryResponse,
    WorkspaceDirectoryStateResponse,
    WorkspaceErrorResponse,
)
from app.services.workspace.directory.directory_picker import select_directory
from app.services.workspace.directory.workspace_binding import bind_workspace_directory

router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute
)


@router.put(
    "/{workspace_id}/directory",
    response_model=WorkspaceDirectoryResponse,
    responses={
        400: {
            "model": WorkspaceErrorResponse,
            "description": "请求无法解析",
        },
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式或本地访问边界拒绝",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "工作空间不存在或不可访问",
        },
        409: {
            "model": WorkspaceErrorResponse,
            "description": "工作空间已绑定其他目录",
        },
        415: {
            "model": WorkspaceErrorResponse,
            "description": "请求必须使用 application/json",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "标识、正文或目录不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "绑定结果未确认",
        },
        503: {
            "model": WorkspaceErrorResponse,
            "description": "文件系统暂时不可用",
        },
    },
)
def bind_workspace_root(
    workspace_id: Annotated[
        str,
        Path(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-f]{32}$",
        ),
    ],
    payload: WorkspaceDirectoryRequest,
    current_user: CurrentUser,
) -> WorkspaceDirectoryResponse:
    """绑定本人工作空间目录；路由管理 Session，服务管理事务。"""

    # 普通 def 在线程池执行，避免同步数据库和文件系统调用阻塞事件循环。
    # 身份依赖已关闭自己的 Session，业务使用新的独立 Session。
    with SessionLocal() as session:
        result = bind_workspace_directory(
            session=session,
            user_id=current_user.id,
            workspace_id=workspace_id,
            root_path=payload.root_path,
        )

    # 服务已提交并返回普通结果，关闭 Session 后仍可组装响应。
    return WorkspaceDirectoryResponse(
        external_id=result.external_id,
        name=result.name,
        root_path=result.root_path,
    )


@router.get(
    "/{workspace_id}/directory",
    response_model=WorkspaceDirectoryStateResponse,
    responses={
        403: {
            "model": WorkspaceErrorResponse,
            "description": "非本地模式或本地访问边界拒绝",
        },
        404: {
            "model": WorkspaceErrorResponse,
            "description": "工作空间不存在或不可访问",
        },
        422: {
            "model": WorkspaceErrorResponse,
            "description": "工作空间标识不符合要求",
        },
        500: {
            "model": WorkspaceErrorResponse,
            "description": "读取目录绑定状态失败",
        },
    },
)
def read_workspace_directory(
    workspace_id: Annotated[
        str,
        Path(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-f]{32}$",
        ),
    ],
    current_user: CurrentUser,
) -> WorkspaceDirectoryStateResponse:
    """读取本人工作空间的绑定记录，不修改数据或访问文件系统。"""

    # 普通 def 在线程池执行，业务 Session 与身份解析 Session 分开。
    with SessionLocal() as session:
        workspace = require_owned_workspace(
            session=session,
            user_id=current_user.id,
            workspace_id=workspace_id,
        )

        # 在 Session 关闭前复制响应字段，不把 ORM 对象交给外层序列化。
        # 必须保留 None，不能替换为空字符串或猜测默认项目目录。
        result = WorkspaceDirectoryStateResponse(
            external_id=workspace.external_id,
            name=workspace.name,
            root_path=workspace.root_path,
        )

    # 只读操作无需 commit；Session 退出时结束读取事务并释放连接。
    return result


@router.post(
    "/{workspace_id}/directory/select", response_model=WorkspaceDirectoryResponse
)
def select_workspace_directory(
    workspace_id: Annotated[
        str, Path(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    ],
    payload: DirectorySelectionRequest,
    current_user: CurrentUser,
) -> WorkspaceDirectoryResponse | Response:
    """用户选择后直接绑定；取消为 204，不产生绑定写入。"""

    # 打开系统窗口前先授权，并在等待用户操作前关闭读取事务。
    with SessionLocal() as session:
        workspace = require_owned_workspace(
            session=session,
            user_id=current_user.id,
            workspace_id=workspace_id,
        )
        if workspace.root_path is not None:
            return WorkspaceDirectoryResponse(
                external_id=workspace.external_id,
                name=workspace.name,
                root_path=workspace.root_path,
            )

    # 同步路由在线程池运行，选择窗口不会阻塞 Agent 的事件循环。
    path = select_directory()
    if path is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    # 用户选择期间可能发生并发绑定，保存时重新检查归属和行锁状态。
    with SessionLocal() as session:
        result = bind_workspace_directory(
            session=session,
            user_id=current_user.id,
            workspace_id=workspace_id,
            root_path=path,
        )
    return WorkspaceDirectoryResponse(
        external_id=result.external_id,
        name=result.name,
        root_path=result.root_path,
    )
