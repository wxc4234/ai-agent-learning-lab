"""Vault 只读 HTTP 入口，复用本地访问与 Workspace 归属边界。"""

from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
)
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.routers.workspace.parameters import TaskIdentifier
from app.services.workspace.directory.workspace_directory import (
    WorkspaceDirectoryError,
)
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.files.vault import (
    VaultDocument,
    VaultError,
    VaultListing,
    list_vault_markdown,
    read_vault_markdown,
)
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.services.workspace.files.workspace_listing import WorkspaceListingError
from app.services.workspace.files.vault_search import (
    VaultSearchResult,
    search_vault_markdown,
)
from app.services.workspace.files.workspace_search import WorkspaceSearchError


router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    route_class=WorkspaceRoute,
)


def _vault_response(
    operation: Callable[
        [],
        VaultListing | VaultDocument | VaultSearchResult,
    ],
) -> Response:
    """统一响应形态；失败不能伪装成空清单、空正文或无匹配。"""

    try:
        result = operation()
        return JSONResponse(
            content=asdict(result),
            headers={"Cache-Control": "no-store"},
        )

    except WorkspaceNotAccessibleError:
        # 交给已有边界返回统一 404，不区分不存在与无权访问。
        raise

    except (
        VaultError,
        WorkspacePathError,
        WorkspaceFileError,
        WorkspaceListingError,
        WorkspaceDirectoryError,
        WorkspaceSearchError,
    ) as error:
        # 这些业务异常只包含固定、安全的错误码和文案。
        # 不将底层系统异常、绝对路径或笔记正文放入错误响应。
        if error.code in {
            "workspace_path_not_found",
            "file_not_found",
            "directory_listing_not_found",
            "directory_not_found",
        }:
            status_code = 404
        elif error.code in {
            "workspace_directory_changed",
            "file_changed",
            "directory_listing_changed",
        }:
            status_code = 409
        elif error.code in {
            "workspace_path_access_denied",
            "file_access_denied",
            "directory_listing_access_denied",
            "directory_access_denied",
        }:
            status_code = 403
        elif error.code in {
            "file_read_unsupported",
            "directory_listing_unsupported",
        }:
            status_code = 501
        elif error.code in {
            "workspace_path_unavailable",
            "file_unavailable",
            "directory_listing_unavailable",
            "directory_unavailable",
        }:
            status_code = 503
        else:
            status_code = 422

        return _error_response(
            status_code,
            code=error.code,
            message=str(error),
        )

    except Exception:  # noqa: BLE001 -- HTTP 边界不暴露内部异常
        return _error_response(
            500,
            code="vault_read_failed",
            message="Vault 读取失败，结果未知",
        )


@router.get("/{workspace_id}/tasks/{task_id}/vault/files")
def list_files(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    request: Request,
    current_user: CurrentUser,
) -> Response:
    """身份来自依赖；客户端不能指定身份、根目录或扫描预算。"""

    if request.query_params:
        return _error_response(
            422,
            code="invalid_vault_input",
            message="Markdown 清单不接受查询参数",
        )

    # 同步数据库和文件操作在线程池中执行，不阻塞事件循环。
    return _vault_response(
        lambda: list_vault_markdown(
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
    )


@router.get("/{workspace_id}/tasks/{task_id}/vault/document")
def read_document(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    request: Request,
    current_user: CurrentUser,
) -> Response:
    """只接受一个相对路径，不允许重复参数或额外身份字段。"""

    pairs = list(request.query_params.multi_items())
    if len(pairs) != 1 or pairs[0][0] != "path":
        return _error_response(
            422,
            code="invalid_vault_input",
            message="Markdown 读取只接受一个 path 查询参数",
        )

    return _vault_response(
        lambda: read_vault_markdown(
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
            relative_path=pairs[0][1],
        )
    )


@router.get("/{workspace_id}/tasks/{task_id}/vault/search")
def search_documents(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    request: Request,
    current_user: CurrentUser,
) -> Response:
    """只接受一个关键词，身份与目录范围由服务端确定。"""

    pairs = list(request.query_params.multi_items())
    if len(pairs) != 1 or pairs[0][0] != "query":
        return _error_response(
            422,
            code="invalid_vault_input",
            message="Vault 搜索只接受一个 query 查询参数",
        )

    # 同步数据库与文件操作在线程池执行，不阻塞事件循环。
    return _vault_response(
        lambda: search_vault_markdown(
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
            query=pairs[0][1],
        )
    )
