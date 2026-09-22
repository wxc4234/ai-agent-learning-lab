"""统一来源、内容类型、验证异常和失败响应边界。"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.repositories.workspace.proposal_application_guard import (
    ProposalApplicationBusyError,
)
from app.repositories.workspace.workspace_repository import (
    InvalidWorkspaceNameError,
    WorkspaceNotAccessibleError,
)
from app.routers.workspace.errors import _workspace_failure_response
from app.routers.workspace.http import _error_response
from app.routers.workspace.request_kinds import (
    _is_directory_request,
    _is_proposal_application_status_request,
    _is_proposal_decision_request,
    _is_proposal_execution_request,
    _is_sample_status_request,
    _is_task_create_request,
    _is_task_delete_request,
    _is_task_run_list_request,
)
from app.services.auth.login_session_resolver import InvalidLoginSessionError
from app.services.runtime.execution.conversation_execution_service import (
    ConversationBusyError,
)
from app.services.tasks.task_deletion_service import TaskRunUnsettledError
from app.services.tasks.task_run_query import InvalidTaskRunQueryError
from app.services.tasks.task_service import (
    InvalidTaskRequestKeyError,
    InvalidTaskTitleError,
    TaskCreationConflictError,
    TaskCreationResultDeletedError,
)
from app.services.workspace.directory.directory_picker import DirectoryPickerError
from app.services.workspace.directory.workspace_binding import (
    WorkspaceAlreadyBoundError,
)
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError


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

                # 只读查询仅使用路径资源，不接受另一套查询身份或正文选项。
                if _is_sample_status_request(request) and (request.query_params or await request.body()):
                    return _error_response(
                        422,
                        code="invalid_sample_status_input",
                        message="样例登记查询不接受查询参数或正文",
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
                if _is_sample_status_request(request):
                    return _error_response(
                        422,
                        code="invalid_sample_status_input",
                        message="样例登记查询参数不符合要求",
                    )
                if _is_proposal_execution_request(request):
                    return _error_response(
                        422,
                        code="invalid_proposal_execution_input",
                        message="提案应用请求参数不符合要求",
                    )
                # 路径和正文校验错误统一脱敏，不返回原始输入。
                if _is_proposal_application_status_request(request):
                    return _error_response(
                        422,
                        code="invalid_proposal_application_status_input",
                        message="提案应用状态查询参数不符合要求",
                    )

                if _is_proposal_decision_request(request):
                    return _error_response(
                        422,
                        code="invalid_proposal_decision_input",
                        message="提案审批请求参数不符合要求",
                    )

                if _is_task_run_list_request(request):
                    return _error_response(
                        422,
                        code=InvalidTaskRunQueryError.code,
                        message="任务运行历史查询参数不符合要求",
                    )

                if _is_task_create_request(request) or _is_task_delete_request(request):
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

            except ProposalApplicationBusyError:
                return _error_response(
                    409,
                    code=ProposalApplicationBusyError.code,
                    message="存在执行中或结果未确认的文件应用，暂不能变更资源",
                )

            except WorkspaceAlreadyBoundError:
                return _error_response(
                    409,
                    code=WorkspaceAlreadyBoundError.code,
                    message="工作空间已绑定其他项目目录",
                )

            except DirectoryPickerError as error:
                picker_errors = {
                    "directory_picker_busy": (
                        409,
                        "已有目录选择窗口，请先完成或取消选择",
                    ),
                    "directory_picker_timeout": (408, "目录选择已超时，请重新选择"),
                    "directory_picker_unsupported": (501, "当前系统暂不支持目录选择"),
                    "directory_picker_unavailable": (
                        503,
                        "无法打开系统目录选择窗口，请检查本地桌面环境",
                    ),
                }
                status_code, message = picker_errors[error.code]
                return _error_response(status_code, code=error.code, message=message)

            except WorkspaceDirectoryError as error:
                # 使用固定映射，不把异常正文直接作为 HTTP 响应。
                directory_errors = {
                    "invalid_directory_path": (
                        422,
                        "项目目录路径不符合要求",
                    ),
                    "directory_not_found": (
                        422,
                        "项目目录不存在或路径中包含非目录项",
                    ),
                    "directory_access_denied": (
                        422,
                        "没有权限访问项目目录",
                    ),
                    "not_a_directory": (
                        422,
                        "请选择目录，而不是文件",
                    ),
                    "root_directory_not_allowed": (
                        422,
                        "不能将文件系统根目录作为项目目录",
                    ),
                    "directory_unavailable": (
                        503,
                        "暂时无法访问项目目录，请稍后再试",
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
