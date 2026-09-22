"""将未知失败映射为操作对应的安全错误，不猜测副作用。"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

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

logger = logging.getLogger(__name__)


def _workspace_failure_response(request: Request) -> JSONResponse:
    """按操作返回安全错误，不暴露SQL、路径或原始异常。"""

    if _is_sample_status_request(request):
        code = "sample_status_read_failed"
        message = "读取样例登记状态失败，请稍后重新查询"
    elif _is_proposal_execution_request(request):
        code = "proposal_execution_uncertain"
        message = "本次执行结果未确认，不能据此判断文件未修改，请勿重复提交"
    elif _is_proposal_application_status_request(request):
        code = "proposal_application_status_read_failed"
        message = "读取提案应用状态失败，请稍后重新查询"
    elif _is_proposal_decision_request(request):
        code = "proposal_decision_uncertain"
        message = "提案决策结果未确认，请先查询详情，勿直接重复提交"
    elif _is_task_create_request(request):
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

    # 日志只记录固定错误码，不包含原始异常、路径或请求数据。
    # 状态查询失败不意味着提案处于idle，也不能触发重新执行。
    logger.error(code)

    return _error_response(
        500,
        code=code,
        message=message,
    )
