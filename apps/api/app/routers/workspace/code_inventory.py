"""本地只读代码/符号清单入口，不接受目录、身份或扫描策略覆盖。"""

from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.dependencies import CurrentUser
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.routers.workspace.boundary import WorkspaceRoute
from app.routers.workspace.http import _error_response
from app.routers.workspace.parameters import TaskIdentifier
from app.services.workspace.directory.workspace_directory import WorkspaceDirectoryError
from app.services.workspace.directory.workspace_path import WorkspacePathError
from app.services.workspace.files.code_ignore import CodeIgnoreError
from app.services.workspace.files.code_inventory import (
    CodeInventoryError,
    CodeInventory,
    scan_code_inventory,
)
from app.services.workspace.files.workspace_file import WorkspaceFileError
from app.services.workspace.files.workspace_listing import WorkspaceListingError
from app.services.workspace.files.python_symbols import (
    PythonSymbolsError,
    PythonSymbolInventory,
    scan_python_symbols,
)
from app.services.workspace.files.python_symbol_search import (
    PythonSymbolSearchResult,
    search_python_symbols,
)


router = APIRouter(
    prefix="/workspaces", tags=["workspaces"], route_class=WorkspaceRoute
)


@router.get("/{workspace_id}/tasks/{task_id}/code-inventory")
def inventory(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    request: Request,
    current_user: CurrentUser,
) -> Response:
    return _inventory_response(
        workspace_id, task_id, request, current_user.id, python_symbols=False
    )


@router.get("/{workspace_id}/tasks/{task_id}/python-symbols")
def symbols(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    request: Request,
    current_user: CurrentUser,
) -> Response:
    return _inventory_response(
        workspace_id, task_id, request, current_user.id, python_symbols=True
    )


@router.get("/{workspace_id}/tasks/{task_id}/python-symbol-search")
def symbol_search(
    workspace_id: TaskIdentifier,
    task_id: TaskIdentifier,
    request: Request,
    current_user: CurrentUser,
) -> Response:
    # 只接受恰好一个query；重复参数和所有范围/策略覆盖都在扫描前拒绝。
    if (
        set(request.query_params) != {"query"}
        or len(request.query_params.getlist("query")) != 1
    ):
        return _error_response(
            422,
            code="invalid_python_symbol_query",
            message="符号检索只接受一个query参数",
        )
    return _code_response(
        lambda: search_python_symbols(
            user_id=current_user.id,
            workspace_id=workspace_id,
            task_id=task_id,
            query=request.query_params["query"],
        ),
        failure_code="python_symbol_search_failed",
        failure_message="符号检索失败，结果未知",
    )


def _inventory_response(
    workspace_id: str,
    task_id: str,
    request: Request,
    user_id: int,
    *,
    python_symbols: bool,
) -> Response:
    # 模式只来自上面的固定路由，客户端不能覆盖扫描策略或解析预算。
    if request.query_params:
        return _error_response(
            422,
            code="invalid_python_symbols_input"
            if python_symbols
            else "invalid_code_inventory_input",
            message="符号清单不接受查询参数"
            if python_symbols
            else "源码清单不接受查询参数",
        )
    scanner = scan_python_symbols if python_symbols else scan_code_inventory
    return _code_response(
        lambda: scanner(user_id=user_id, workspace_id=workspace_id, task_id=task_id),
        failure_code="python_symbols_failed"
        if python_symbols
        else "code_inventory_failed",
        failure_message="符号扫描失败，结果未知"
        if python_symbols
        else "源码扫描失败，结果未知",
    )


def _code_response(
    operation: Callable[
        [], CodeInventory | PythonSymbolInventory | PythonSymbolSearchResult
    ],
    *,
    failure_code: str,
    failure_message: str,
) -> Response:
    try:
        # 同步处理在线程池执行，Session由服务在I/O前关闭；不创建索引或提交事务。
        result = operation()
        return JSONResponse(asdict(result), headers={"Cache-Control": "no-store"})
    except WorkspaceNotAccessibleError:
        raise  # 沿用边界统一404，不区分不存在与无权访问。
    except (
        CodeInventoryError,
        CodeIgnoreError,
        PythonSymbolsError,
        WorkspacePathError,
        WorkspaceFileError,
        WorkspaceListingError,
        WorkspaceDirectoryError,
    ) as error:
        if error.code in {
            "code_ignore_changed",
            "workspace_directory_changed",
            "file_changed",
            "directory_listing_changed",
        }:
            status = 409
        elif error.code in {
            "workspace_path_not_found",
            "file_not_found",
            "directory_listing_not_found",
            "directory_not_found",
        }:
            status = 404
        elif error.code in {
            "workspace_path_access_denied",
            "file_access_denied",
            "directory_listing_access_denied",
            "directory_access_denied",
        }:
            status = 403
        elif error.code in {"file_read_unsupported", "directory_listing_unsupported"}:
            status = 501
        elif error.code in {
            "code_ignore_unavailable",
            "workspace_path_unavailable",
            "file_unavailable",
            "directory_listing_unavailable",
            "directory_unavailable",
        }:
            status = 503
        else:
            status = 422
        return _error_response(status, code=error.code, message=str(error))
    except Exception:  # noqa: BLE001 -- 不反射宿主路径、配置或源码正文。
        return _error_response(
            500,
            code=failure_code,
            message=failure_message,
        )
