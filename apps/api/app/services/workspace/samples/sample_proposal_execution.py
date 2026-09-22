"""仅允许可信服务端样例进入内部执行器；不开放HTTP或模型工具。"""

from pydantic import ValidationError

from app.database import SessionLocal
from app.schemas import FileEditProposalExecutionResponse
from app.repositories.workspace.file_edit_proposal_repository import read_owned_proposal_application_source
from app.services.workspace.proposals.file_edit_proposal_execution import (
    ProposalExecutionResult,
    execute_task_file_edit_proposal,
)
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings


class SampleExecutionError(ValueError):
    def __init__(self) -> None:
        super().__init__('sample_execution_unavailable')


class _SealBinding(Exception):
    """仅用于退出借用时封锁登记；不删除仍可能被数据库引用的目录。"""


def execute_sample_proposal(
    bindings: TaskSampleBindings, *, user_id: int, workspace_id: str,
    task_id: str, proposal_id: str,
) -> ProposalExecutionResult:
    """可信调用方持有长生命周期bindings；浏览器不能选择登记对象或路径。"""
    identity = {"user_id": user_id, "workspace_id": workspace_id, "task_id": task_id,
                "proposal_id": proposal_id}
    receipt = None
    try:
        with bindings.borrow(user_id=user_id, workspace_id=workspace_id, task_id=task_id) as sample:
            # 独立授权提案：任务登记本身不证明此提案属于当前任务。
            with SessionLocal() as session:
                source = read_owned_proposal_application_source(session, **identity)
                if (
                    source['bound_root'] != str(sample.root)
                    or source['current_root'] != str(sample.root)
                    or source['relative_path'] != sample.relative_path
                    or source['status'] != 'approved'
                    or source['diff_truncated']
                ):
                    raise SampleExecutionError()
            # 期望范围贯穿预检和实际写入，不依赖可能变化的数据库路径。
            receipt = execute_task_file_edit_proposal(
                **identity, expected_bound_root=str(sample.root),
                expected_relative_path=sample.relative_path,
                expected_parent_identity=sample.root_identity,
            )
            if type(receipt) is not ProposalExecutionResult or receipt.proposal_id != proposal_id:
                raise SampleExecutionError()
            try:
                FileEditProposalExecutionResponse.model_validate({
                    "workspace_id": workspace_id, "task_id": task_id,
                    "proposal_id": receipt.proposal_id, "file_status": receipt.file_status,
                    "application_status": receipt.application_status, "code": receipt.code,
                    "cleanup_complete": receipt.cleanup_complete,
                })
            except ValidationError:
                raise SampleExecutionError() from None
            if receipt.application_status not in ('applied', 'not_applied'):
                # 未确认结果保留文件证据，但封锁本地登记，不能正常归还再使用。
                raise _SealBinding()
    except _SealBinding:
        if receipt is None:
            raise SampleExecutionError() from None
        return receipt
    return receipt
