"""调用方持有Workspace锁时，检查资源下尚未确认结束的应用。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FileEditProposal, Task


class ProposalApplicationBusyError(Exception):
    """明确未执行资源变更；不携带提案正文或执行令牌。"""

    code = 'proposal_application_busy'

    def __init__(self) -> None:
        super().__init__('存在执行中或结果未确认的文件应用，暂不能变更资源')


def require_no_active_proposal_application(
    session: Session, *, workspace_id: int, task_id: int | None = None,
) -> None:
    """必须先取得已授权Workspace行锁，并保持到资源变更提交结束。"""

    # 领取和完成登记也先取得Workspace锁，因此此查询之后不能插入新的占用。
    # 不按时间释放running，也不能将uncertain当成失败；不读取令牌或正文。
    statement = select(FileEditProposal.id).join(Task).where(
        Task.workspace_id == workspace_id,
        FileEditProposal.application_status.in_(('running', 'uncertain')),
    ).limit(1)
    if task_id is not None:
        statement = statement.where(Task.id == task_id)
    with session.no_autoflush:
        if session.scalar(statement) is not None:
            raise ProposalApplicationBusyError()
