"""单次应用占用与结果登记；不读取或写入文件，不提供公开执行入口。"""

from dataclasses import dataclass, field
import re
from typing import Literal
from uuid import uuid4

from app.database import SessionLocal
from app.repositories.workspace.file_edit_proposal_repository import (
    lock_file_edit_proposal_for_decision,
    lock_owned_proposal_task,
)
from app.services.workspace.proposals.file_edit_proposal_service import ProposalBindingChangedError

ApplicationOutcome = Literal['applied', 'not_applied', 'uncertain']


class ProposalApplicationError(ValueError):
    """只返回固定错误，不回显执行令牌。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ProposalApplicationResult:
    """内部执行回执；令牌隐藏于repr，仍不得序列化为公开响应。"""

    proposal_id: str
    workspace_id: str
    task_id: str
    application_status: str
    application_token: str = field(repr=False)


def claim_task_file_edit_proposal(
    *, user_id: int, workspace_id: str, task_id: str, proposal_id: str,
) -> ProposalApplicationResult:
    """单次领取；返回成功之后，未来执行器仍必须重新核对文件和授权。"""

    # 与审批/删除统一Workspace→Task→Conversation→Proposal锁序。
    # 此事务只操作数据库，不能在行锁内执行文件I/O。
    with SessionLocal.begin() as session:
        workspace, task = lock_owned_proposal_task(
            session, user_id=user_id, workspace_id=workspace_id, task_id=task_id,
        )
        proposal = lock_file_edit_proposal_for_decision(session, task_id=task.id, proposal_id=proposal_id)
        if proposal.status != 'approved' or proposal.diff_truncated:
            raise ProposalApplicationError('proposal_not_approved', '提案未满足批准条件')
        if proposal.application_status != 'idle':
            raise ProposalApplicationError('proposal_application_conflict', '提案已有应用执行记录，不能再次启动')
        if workspace.root_path is None or workspace.root_path != proposal.bound_root:
            raise ProposalBindingChangedError()
        token = uuid4().hex
        proposal.application_status = 'running'
        proposal.application_token = token
        session.flush()
        result = ProposalApplicationResult(proposal_id, workspace_id, task_id, 'running', token)
    # 提交失败不返回令牌、不自动重试；未知领取结果不得开始文件写入。
    return result


def finish_task_file_edit_proposal(
    *, user_id: int, workspace_id: str, task_id: str, proposal_id: str,
    application_token: str, outcome: ApplicationOutcome,
) -> ProposalApplicationResult:
    """仅供可信执行器登记结果；not_applied必须有确认未写入的证据。"""

    if not isinstance(application_token, str) or re.fullmatch(r'[0-9a-f]{32}', application_token) is None:
        raise ProposalApplicationError('proposal_application_token_invalid', '应用执行凭据无效')
    if not isinstance(outcome, str) or outcome not in ('applied', 'not_applied', 'uncertain'):
        raise ProposalApplicationError('proposal_application_outcome_invalid', '应用结果类型无效')
    with SessionLocal.begin() as session:
        _, task = lock_owned_proposal_task(
            session, user_id=user_id, workspace_id=workspace_id, task_id=task_id,
        )
        proposal = lock_file_edit_proposal_for_decision(session, task_id=task.id, proposal_id=proposal_id)
        if (proposal.application_status != 'running'
                or proposal.application_token != application_token):
            raise ProposalApplicationError('proposal_application_conflict', '应用执行状态或凭据不匹配')
        # 不要求绑定仍相同：执行后的目录变化不能阻止登记已发生的结果。
        # 归属仍重新授权；失败时保持原状态，不能据此推断文件未写入。
        proposal.application_status = outcome
        session.flush()
        result = ProposalApplicationResult(proposal_id, workspace_id, task_id, outcome, application_token)
    # 不接受终态重放或uncertain重新领取，也没有超时回收。
    return result
