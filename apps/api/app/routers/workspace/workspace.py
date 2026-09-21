"""Workspace 创建接口：身份、来源校验与安全响应边界。"""

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict

from fastapi import APIRouter, Query, Path, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.tasks.task_workspace import task_list, task_messages, summarize_title
from app.config import settings
from app.database import SessionLocal
from app.dependencies import CurrentUser
from app.repositories.workspace.workspace_repository import (
    InvalidWorkspaceNameError,
    WorkspaceNotAccessibleError,
    list_owned_workspaces,
    require_owned_workspace,
)
from app.schemas import (
    WorkspaceCreateRequest,
    WorkspaceDirectoryRequest,
    WorkspaceDirectoryResponse,
    WorkspaceDirectoryStateResponse,
    WorkspaceErrorResponse,
    WorkspaceListResponse,
    WorkspaceResponse,
    TaskCreateRequest,
    TaskResponse,
)
from app.services.auth.login_session_resolver import InvalidLoginSessionError
from app.services.workspace.workspace_service import create_user_workspace
from app.services.workspace.workspace_binding import (
    WorkspaceAlreadyBoundError,
    bind_workspace_directory,
)
from app.services.workspace.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.directory_picker import DirectoryPickerError, select_directory
from app.services.tasks.task_service import (
    InvalidTaskRequestKeyError,
    InvalidTaskTitleError,
    TaskCreationConflictError,
    TaskCreationResultDeletedError,
    create_workspace_task,
)
from app.services.runtime.execution.conversation_execution_service import ConversationBusyError
from app.services.tasks.task_deletion_service import (
    TaskRunUnsettledError,
    delete_workspace_task,
)
from app.schemas import (
    FileEditProposalDetailResponse,
    TaskDetailResponse,
    TaskRunListResponse,
)
from app.services.workspace.file_edit_proposal_service import (
    get_task_file_edit_proposal,
)
from app.services.tasks.task_run_query import (
    MAX_PAGE_SIZE,
    MAX_RUN_ID,
    InvalidTaskRunQueryError,
    list_task_runs,
)
from app.services.tasks.task_workspace import task_detail

logger = logging.getLogger(__name__)


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
) -> JSONResponse:
    """显式构建安全正文，不序列化原始异常或请求输入。"""

    payload = WorkspaceErrorResponse(
        code=code,
        message=message,
    )

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"Cache-Control": "no-store"},
    )

def _is_directory_request(request: Request) -> bool:
    """依据已经匹配的路由模板识别目录接口，不检查用户输入的路径后缀。"""

    route = request.scope.get("route")

    # 目录读取、兼容 PUT 绑定与系统选择统一应用本地模式边界。
    return (
        getattr(route, "path", None)
        in {"/workspaces/{workspace_id}/directory", "/workspaces/{workspace_id}/directory/select"}
    )

def _is_task_create_request(request: Request) -> bool:
    """根据已匹配的路由模板识别任务创建，不猜测 URL 后缀。"""

    route = request.scope.get("route")

    return (
        request.method == "POST"
        and getattr(route, "path", None)
        == "/workspaces/{workspace_id}/tasks"
    )

def _is_task_delete_request(request: Request) -> bool:
    """使用已匹配的路由模板识别任务删除，不猜测实际 URL。"""

    route = request.scope.get("route")

    return (
        request.method == "DELETE"
        and getattr(route, "path", None)
        == "/workspaces/{workspace_id}/tasks/{task_id}"
    )

def _is_task_run_list_request(request: Request) -> bool:
    """依据已匹配的路由模板识别运行列表，避免依赖用户输入的路径内容。"""

    route = request.scope.get("route")

    return (
        request.method == "GET"
        and getattr(route, "path", None)
        == "/workspaces/{workspace_id}/tasks/{task_id}/runs"
    )

def _workspace_failure_response(request: Request) -> JSONResponse:
    """按操作返回安全错误，不暴露 SQL、路径或原始异常。"""

    if _is_task_create_request(request):
        code = "task_creation_uncertain"
        message = "任务创建结果未确认，请勿直接重复提交"
    elif _is_task_delete_request(request):
        code = "task_deletion_uncertain"
        message = "任务删除结果未确认，请刷新任务列表后确认"
    elif _is_task_run_list_request(request):
        code = "task_run_list_failed"
        message = "读取任务运行历史失败，请稍后重试"
    elif request.method == "GET" and _is_directory_request(request):
        code = "workspace_directory_read_failed"
        message = "读取项目目录状态失败，请稍后重试"
    elif request.method == "GET":
        code = "workspace_list_failed"
        message = "读取工作空间列表失败，请稍后再试"
    elif request.method == "PUT" or _is_directory_request(request):
        code = "workspace_binding_failed"
        message = "项目目录绑定结果未确认，请稍后检查"
    else:
        code = "workspace_creation_failed"
        message = "创建工作空间失败，请稍后再试"

    # 写操作发生未知异常时，不能推断数据库一定没有提交；
    # 读操作只报告读取失败。日志和响应均不拼接原始异常内容。
    logger.error(code)

    return _error_response(
        500,
        code=code,
        message=message,
    )

class WorkspaceRoute(APIRoute):
    """覆盖依赖求解、路由执行以及响应生成阶段的错误。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:

                # Task 主流程目前仅服务本地工作台。
                # 在身份解析和数据库操作之前拒绝非本地模式。
                if (
                    "/tasks" in getattr(request.scope.get("route"), "path", "")
                    and settings.app_mode != "local"
                ):
                    return _error_response(
                        403,
                        code="local_mode_required",
                        message="任务功能仅支持本地模式",
                    )

                # 目录读取和绑定都仅供本地工作台使用。
                # 在身份解析及数据库查询前拒绝非本地模式。
                if _is_directory_request(request) and settings.app_mode != "local":
                    return _error_response(
                        403,
                        code="local_mode_required",
                        message="项目目录功能仅支持本地模式",
                    )

                if _is_task_delete_request(request):
                    # 删除也是写操作，即使没有 JSON 正文，也必须检查来源。
                    # 内部凭证和本机 Host 继续由既有本地访问边界检查。
                    origin = request.headers.get("origin")

                    if origin not in settings.login_allowed_origins:
                        return _error_response(
                            403,
                            code="workspace_origin_rejected",
                            message="工作空间请求来源不被允许",
                        )

                    # 删除目标完全由路径确定，不接受正文中的身份或资源字段。
                    # 不要求 Content-Type，也不把空 JSON 对象当作无正文。
                    if await request.body():
                        return _error_response(
                            422,
                            code="invalid_task_input",
                            message="任务删除请求不接受正文",
                        )

                # 创建和绑定都是写操作，都要求可信来源与 JSON 正文。
                if request.method in {"POST", "PUT"}:
                    # 沿用现有允许来源配置，精确匹配，不使用前缀匹配。
                    origin = request.headers.get("origin")

                    if origin not in settings.login_allowed_origins:
                        return _error_response(
                            403,
                            code="workspace_origin_rejected",
                            message="工作空间请求来源不被允许",
                        )

                    # 接受 application/json; charset=utf-8 等合法参数形式。
                    content_type = (
                        request.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )

                    if content_type != "application/json":
                        return _error_response(
                            415,
                            code="unsupported_workspace_content_type",
                            message="工作空间请求必须使用 application/json",
                        )

                # 原始 handler 会解析请求、执行 CurrentUser 依赖并调用接口。
                response = await original_handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response

            except InvalidLoginSessionError:
                return _error_response(
                    401,
                    code="invalid_login_session",
                    message="登录状态无效，请重新登录",
                )

            except RequestValidationError:
                # 路径、查询参数和正文校验错误统一脱敏，不反射原始输入。
                if _is_task_run_list_request(request):
                    return _error_response(
                        422,
                        code=InvalidTaskRunQueryError.code,
                        message="任务运行历史查询参数不符合要求",
                    )

                if (
                    _is_task_create_request(request)
                    or _is_task_delete_request(request)
                ):
                    return _error_response(
                        422,
                        code="invalid_task_input",
                        message="任务请求参数不符合要求",
                    )

                return _error_response(
                    422,
                    code="invalid_workspace_input",
                    message="工作空间请求参数不符合要求",
                )

            except InvalidTaskRunQueryError:
                # 服务层也会校验参数，统一映射为同一份安全 HTTP 契约。
                return _error_response(
                    422,
                    code=InvalidTaskRunQueryError.code,
                    message="任务运行历史查询参数不符合要求",
                )

            except InvalidTaskRequestKeyError:
                # 正文通常先被 Pydantic 校验；服务层拒绝也须有安全映射。
                # 不输出原始请求键或异常正文。
                return _error_response(
                    422,
                    code=InvalidTaskRequestKeyError.code,
                    message="任务创建请求键须为 32 位小写十六进制字符串",
                )

            except TaskCreationConflictError:
                # 原键已经对应另一份内容，不能替换原请求记录。
                return _error_response(
                    409,
                    code=TaskCreationConflictError.code,
                    message="该请求键已用于不同的任务创建内容",
                )

            except TaskCreationResultDeletedError:
                # 原结果已删除，但请求键仍然保留。
                # 不把旧请求当成新创建，也不在路由中自动更换请求键。
                return _error_response(
                    409,
                    code=TaskCreationResultDeletedError.code,
                    message="该请求对应的任务已删除，请使用新的请求键创建",
                )

            except InvalidTaskTitleError:
                # 字符串类型正确但规范化后标题非法，保留业务错误分类。
                return _error_response(
                    422,
                    code=InvalidTaskTitleError.code,
                    message="任务标题去除首尾空白后须为 1～200 个字符",
                )

            except InvalidWorkspaceNameError:
                # 业务校验与请求类型校验都归为 422，但保留不同错误码。
                return _error_response(
                    422,
                    code=InvalidWorkspaceNameError.code,
                    message="工作空间名称去除首尾空白后须为 1～100 个字符",
                )

            except WorkspaceNotAccessibleError:
                # 未知与不可访问资源返回相同结果。
                return _error_response(
                    404,
                    code=WorkspaceNotAccessibleError.code,
                    message="工作空间不存在或不可访问",
                )

            except ConversationBusyError:
                # 删除服务在写入前拒绝；不将占用冲突误报为结果未确认。
                return _error_response(
                    409,
                    code=ConversationBusyError.code,
                    message="该任务仍有执行占用，请等待执行及收尾完成后重试",
                )

            except TaskRunUnsettledError:
                # 服务在执行 DELETE 前拒绝，属于明确未删除的业务冲突。
                # 使用固定文案，不直接输出异常字符串。
                return _error_response(
                    409,
                    code=TaskRunUnsettledError.code,
                    message="存在未确认结束的运行，暂不能删除",
                )

            except WorkspaceAlreadyBoundError:
                return _error_response(
                    409,
                    code=WorkspaceAlreadyBoundError.code,
                    message="工作空间已绑定其他项目目录",
                )

            except DirectoryPickerError as error:
                picker_errors = {
                    "directory_picker_busy": (409, "已有目录选择窗口，请先完成或取消选择"),
                    "directory_picker_timeout": (408, "目录选择已超时，请重新选择"),
                    "directory_picker_unsupported": (501, "当前系统暂不支持目录选择"),
                    "directory_picker_unavailable": (503, "无法打开系统目录选择窗口，请检查本地桌面环境"),
                }
                status_code, message = picker_errors[error.code]
                return _error_response(status_code, code=error.code, message=message)

            except WorkspaceDirectoryError as error:
                # 使用固定映射，不把异常正文直接作为 HTTP 响应。
                directory_errors = {
                    "invalid_directory_path": (
                        422, "项目目录路径不符合要求",
                    ),
                    "directory_not_found": (
                        422, "项目目录不存在或路径中包含非目录项",
                    ),
                    "directory_access_denied": (
                        422, "没有权限访问项目目录",
                    ),
                    "not_a_directory": (
                        422, "请选择目录，而不是文件",
                    ),
                    "root_directory_not_allowed": (
                        422, "不能将文件系统根目录作为项目目录",
                    ),
                    "directory_unavailable": (
                        503, "暂时无法访问项目目录，请稍后再试",
                    ),
                }

                mapped_error = directory_errors.get(error.code)

                # 未知错误码按内部失败处理，不能直接反射给客户端。
                if mapped_error is None:
                    return _workspace_failure_response(request)

                status_code, message = mapped_error

                return _error_response(
                    status_code,
                    code=error.code,
                    message=message,
                )
            except StarletteHTTPException as error:
                if error.status_code == 400:
                    return _error_response(
                        400,
                        code="invalid_workspace_request",
                        message="工作空间请求无法解析",
                    )

                return _workspace_failure_response(request)

            except Exception:  # noqa: BLE001 -- HTTP 边界统一脱敏
                return _workspace_failure_response(request)

        return safe_handler


router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    route_class=WorkspaceRoute,
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


class DirectorySelectionRequest(BaseModel):
    """选择行为不接受浏览器提供路径、命令或身份字段。"""

    model_config = ConfigDict(extra="forbid")


@router.post("/{workspace_id}/directory/select", response_model=WorkspaceDirectoryResponse)
def select_workspace_directory(
    workspace_id: Annotated[str, Path(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")],
    payload: DirectorySelectionRequest,
    current_user: CurrentUser,
) -> WorkspaceDirectoryResponse | Response:
    """用户选择后直接绑定；取消为 204，不产生绑定写入。"""

    # 打开系统窗口前先授权，并在等待用户操作前关闭读取事务。
    with SessionLocal() as session:
        workspace = require_owned_workspace(
            session=session, user_id=current_user.id, workspace_id=workspace_id,
        )
        if workspace.root_path is not None:
            return WorkspaceDirectoryResponse(
                external_id=workspace.external_id, name=workspace.name,
                root_path=workspace.root_path,
            )

    # 同步路由在线程池运行，选择窗口不会阻塞 Agent 的事件循环。
    path = select_directory()
    if path is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    # 用户选择期间可能发生并发绑定，保存时重新检查归属和行锁状态。
    with SessionLocal() as session:
        result = bind_workspace_directory(
            session=session, user_id=current_user.id,
            workspace_id=workspace_id, root_path=path,
        )
    return WorkspaceDirectoryResponse(
        external_id=result.external_id, name=result.name, root_path=result.root_path,
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


# 工作台只接受公开项目/任务标识，归属检查沿 Workspace 与 Conversation 双重确认。

TaskIdentifier = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


@router.get("/{workspace_id}/tasks")
def read_tasks(workspace_id: TaskIdentifier, current_user: CurrentUser,
    before: Annotated[int | None, Query(ge=1, le=2147483647)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20):
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
def read_task_messages(workspace_id: TaskIdentifier, task_id: TaskIdentifier, current_user: CurrentUser):
    return task_messages(current_user.id, workspace_id, task_id)


@router.post("/{workspace_id}/tasks/{task_id}/title")
async def update_task_title(workspace_id: TaskIdentifier, task_id: TaskIdentifier,
    payload: DirectorySelectionRequest, current_user: CurrentUser):
    return await summarize_title(current_user.id, workspace_id, task_id)
