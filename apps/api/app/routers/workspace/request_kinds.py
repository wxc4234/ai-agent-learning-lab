"""识别请求所属操作，供统一安全边界选择错误契约。"""

from fastapi import Request


def _is_directory_request(request: Request) -> bool:
    """依据已经匹配的路由模板识别目录接口，不检查用户输入的路径后缀。"""

    route = request.scope.get("route")

    # 目录读取、兼容 PUT 绑定与系统选择统一应用本地模式边界。
    return getattr(route, "path", None) in {
        "/workspaces/{workspace_id}/directory",
        "/workspaces/{workspace_id}/directory/select",
    }


def _is_task_create_request(request: Request) -> bool:
    """根据已匹配的路由模板识别任务创建，不猜测 URL 后缀。"""

    route = request.scope.get("route")

    return (
        request.method == "POST"
        and getattr(route, "path", None) == "/workspaces/{workspace_id}/tasks"
    )


def _is_task_delete_request(request: Request) -> bool:
    """使用已匹配的路由模板识别任务删除，不猜测实际 URL。"""

    route = request.scope.get("route")

    return (
        request.method == "DELETE"
        and getattr(route, "path", None) == "/workspaces/{workspace_id}/tasks/{task_id}"
    )


def _is_task_run_list_request(request: Request) -> bool:
    """依据已匹配的路由模板识别运行列表，避免依赖用户输入的路径内容。"""

    route = request.scope.get("route")

    return (
        request.method == "GET"
        and getattr(route, "path", None)
        == "/workspaces/{workspace_id}/tasks/{task_id}/runs"
    )


def _is_proposal_decision_request(request: Request) -> bool:
    """通过已匹配的路由模板识别审批，不依赖用户填写的实际路径。"""

    route = request.scope.get("route")

    return request.method == "POST" and getattr(route, "path", None) == (
        "/workspaces/{workspace_id}/tasks/{task_id}"
        "/file-edit-proposals/{proposal_id}/decision"
    )


def _is_proposal_application_status_request(request: Request) -> bool:
    """通过匹配后的路由模板识别查询，不根据实际URL后缀猜测。"""

    route = request.scope.get("route")

    return request.method == "GET" and getattr(route, "path", None) == (
        "/workspaces/{workspace_id}/tasks/{task_id}"
        "/file-edit-proposals/{proposal_id}/application-status"
    )


def _is_proposal_execution_request(request: Request) -> bool:
    """识别已匹配的执行路由，不根据用户填写的URL后缀猜测。"""

    route = request.scope.get("route")

    return request.method == "POST" and getattr(route, "path", None) == (
        "/workspaces/{workspace_id}/tasks/{task_id}"
        "/file-edit-proposals/{proposal_id}/apply"
    )


def _is_sample_status_request(request: Request) -> bool:
    """按匹配后的模板识别登记查询，不信任用户填写的URL后缀。"""
    return request.method == "GET" and getattr(request.scope.get("route"), "path", None) == (
        "/workspaces/{workspace_id}/tasks/{task_id}/sample-status"
    )
