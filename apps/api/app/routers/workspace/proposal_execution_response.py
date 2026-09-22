"""执行回执的公开投影；不授权资源、不执行文件操作、不登记状态。"""

from pydantic import ValidationError

from app.schemas import FileEditProposalExecutionResponse
from app.services.workspace.proposals.file_edit_proposal_execution import (
    ProposalExecutionResult,
)


class ProposalExecutionResponseError(ValueError):
    """固定公开错误，不携带原始回执或校验失败的输入内容。"""

    code = "proposal_execution_response_invalid"

    def __init__(self) -> None:
        super().__init__(self.code)


def build_proposal_execution_response(
    *,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
    result: ProposalExecutionResult,
) -> FileEditProposalExecutionResponse:
    """由可信调用方传入本次操作的资源标识，显式投影允许公开的字段。

    此处没有数据库事务或文件操作。
    转换失败不能解释为执行失败，更不能触发重新执行。
    """

    # 类型注解不会执行运行时检查，拒绝错误对象或非预期的子类。
    if type(result) is not ProposalExecutionResult:
        raise ProposalExecutionResponseError()

    # 响应必须对应本次操作的提案，不能用请求编号覆盖错误回执编号。
    if (
        type(proposal_id) is not str
        or type(result.proposal_id) is not str
        or result.proposal_id != proposal_id
    ):
        raise ProposalExecutionResponseError()

    try:
        # 只列出允许公开的字段，不使用 __dict__、asdict 或整体展开。
        # 未来内部回执增加令牌或调试信息时，也不会自动进入响应。
        return FileEditProposalExecutionResponse.model_validate(
            {
                "workspace_id": workspace_id,
                "task_id": task_id,
                "proposal_id": result.proposal_id,
                "file_status": result.file_status,
                "application_status": result.application_status,
                "code": result.code,
                "cleanup_complete": result.cleanup_complete,
            },
        )
    except ValidationError:
        # Pydantic 校验错误可能包含原始输入，不将其作为公开错误返回。
        raise ProposalExecutionResponseError() from None
