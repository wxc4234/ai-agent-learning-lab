"""查询已授权会话的执行占用快照，不修改占用或运行状态。"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ConversationExecutionSlot
from app.repositories.chat.conversation_repository import (
    require_owned_conversation,
)


@dataclass(frozen=True)
class ConversationExecutionStatus:
    """可供后续 HTTP 层使用的普通数据，不携带 ORM 对象。"""

    session_id: str

    # 表示查询时存在占用记录，不代表执行进程一定存活。
    occupied: bool

    # 没有占用时为 None；该时间不能作为自动过期或强制释放依据。
    acquired_at: datetime | None


def get_conversation_execution_status(
    *,
    user_id: int,
    session_id: str,
) -> ConversationExecutionStatus:
    """先授权，再读取会话占用；数据库异常由调用方处理。"""

    # 本次查询独立创建和关闭 Session。
    # SELECT 会开启只读用途的事务，退出上下文时结束事务，无需提交。
    with SessionLocal() as session:
        # 复用既有会话边界；本地模式下同时检查 Task 和 Workspace 归属。
        # 不存在或不可访问的会话统一抛错，不能伪装成“没有占用”。
        conversation = require_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
        )

        # 只选择允许对外提供的时间字段。
        # 不读取 owner_token，也不加行锁或修改占用记录。
        acquired_at = session.scalar(
            select(ConversationExecutionSlot.acquired_at).where(
                ConversationExecutionSlot.conversation_id == conversation.id,
            )
        )

        # acquired_at 在数据库中不可为空：
        # 查询结果为 None 表示本次查询没有找到占用记录。
        status = ConversationExecutionStatus(
            session_id=session_id,
            occupied=acquired_at is not None,
            acquired_at=acquired_at,
        )

    # Session 已关闭，返回的数据仍可直接使用。
    return status
