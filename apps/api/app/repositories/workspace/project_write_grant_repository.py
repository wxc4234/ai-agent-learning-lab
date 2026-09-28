"""许可记录访问；调用方负责归属授权、提案锁及事务提交。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProjectWriteGrantRecord


def find_project_write_grant(session: Session, proposal_id: int) -> ProjectWriteGrantRecord | None:
    # 写操作先取得同一提案锁；唯一约束是绕过服务时的最后防线。
    return session.scalar(select(ProjectWriteGrantRecord).where(
        ProjectWriteGrantRecord.proposal_id == proposal_id,
    ).execution_options(populate_existing=True))
