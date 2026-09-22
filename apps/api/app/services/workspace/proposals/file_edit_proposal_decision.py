"""记录用户对文件修改提案的决策，不读取或写入项目文件。"""

from dataclasses import dataclass
from typing import Literal

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import (
    lock_file_edit_proposal_for_decision,
    lock_owned_proposal_task,
)
from app.services.workspace.proposals.file_edit_proposal_service import (
    ProposalBindingChangedError,
)


ProposalDecision = Literal["approved", "rejected"]


class ProposalDecisionError(ValueError):
    """决策未满足业务条件；错误消息不得包含提案正文或宿主路径。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class FileEditProposalDecisionResult:
    """仅在事务提交成功后返回的决策结果。"""

    proposal_id: str
    workspace_id: str
    task_id: str
    status: ProposalDecision


def decide_task_file_edit_proposal(
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
    proposal_id: str,
    decision: ProposalDecision,
) -> FileEditProposalDecisionResult:
    """重新授权并串行化同一提案的决策，只允许从pending转换一次。"""

    # Literal只提供静态类型提示，运行时仍需拒绝非法调用。
    if not isinstance(decision, str) or decision not in (
        "approved",
        "rejected",
    ):
        raise ProposalDecisionError(
            "proposal_decision_invalid",
            "提案决策只能是批准或拒绝",
        )

    # 服务独占一个新Session和写事务；任何异常都会触发回滚。
    # 归属检查与更新处于同一事务，不信任此前详情查询的授权结果。
    with SessionLocal.begin() as session:
        workspace, task = lock_owned_proposal_task(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        proposal = lock_file_edit_proposal_for_decision(
            session,
            task_id=task.id,
            proposal_id=proposal_id,
        )

        # 必须在取得锁后检查；等待其他事务结束后读取最新状态。
        # 同方向重复请求也冲突，不能将重复调用伪装成本次成功。
        if proposal.status != "pending":
            raise ProposalDecisionError(
                "proposal_state_conflict",
                "提案已不处于待审批状态，请重新读取详情",
            )

        if decision == "approved":
            # 批准绑定到保存时的目录；这不证明目录对象或文件基线未变化。
            # 拒绝不依赖目录仍可用，允许关闭已经失效的提案。
            if workspace.root_path != proposal.bound_root:
                raise ProposalBindingChangedError()

            if proposal.diff_truncated:
                raise ProposalDecisionError(
                    "proposal_diff_incomplete",
                    "提案Diff已截断，不能直接批准",
                )

        proposal.status = decision

        # flush让数据库检查状态与Diff约束，但此时还不代表提交成功。
        session.flush()

        # 只复制公开定位信息，不把ORM对象或完整新文件内容带出事务。
        result = FileEditProposalDecisionResult(
            proposal_id=proposal.external_id,
            workspace_id=workspace.external_id,
            task_id=task.external_id,
            status=decision,
        )

    # 只有退出begin且提交成功后才返回。
    # 提交异常向上传播，不自动重试，也不返回成功。
    return result
