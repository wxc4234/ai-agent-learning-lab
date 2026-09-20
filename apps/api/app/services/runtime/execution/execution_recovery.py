"""仅凭本机进程退出证据恢复遗留占用，不重放模型或工具。"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AgentRun, AgentRunEvent, ConversationExecutionSlot
from app.repositories.chat.conversation_repository import require_owned_conversation
from app.services.runtime.execution.execution_process import process_is_dead


class ExecutionRecoveryRefusedError(Exception):
    """没有足够停止证据；不能通过恢复接管仍活跃的执行。"""


def recover_conversation_execution(*, user_id: int, session_id: str) -> None:
    if settings.app_mode != 'local':
        raise ExecutionRecoveryRefusedError()
    with SessionLocal.begin() as session:
        # 与获取/释放占用共用会话锁，查询结果不依赖浏览器的旧快照。
        conversation = require_owned_conversation(
            session, user_id=user_id, session_id=session_id, for_update=True,
        )
        slot = session.get(ConversationExecutionSlot, conversation.id)
        runs = session.scalars(
            select(AgentRun).where(AgentRun.conversation_id == conversation.id)
            .order_by(AgentRun.id).with_for_update()
        ).all()
        if slot is not None and not process_is_dead(slot.owner_host_id, slot.owner_pid):
            raise ExecutionRecoveryRefusedError()
        pending = [run for run in runs if run.status == 'running']
        # Run 自身的身份覆盖提交确认丢失；不能借另一个旧占用推断其执行者。
        if any(not process_is_dead(run.owner_host_id, run.owner_pid) for run in pending):
            raise ExecutionRecoveryRefusedError()
        for run in runs:
            if run.status == 'running':
                run.status = 'aborted'
                run.finished_at = datetime.now(timezone.utc)
                # 复用已知终态；额外审计事件记录原因，既有终态绝不覆盖。
                session.add(AgentRunEvent(run_id=run.id, event_type='RUN_ABORTED', payload={'reason': 'unknown'}))
                session.add(AgentRunEvent(run_id=run.id, event_type='EXECUTION_RECOVERED', payload={'reason': 'owner_process_exited'}))
        # 终态、审计和精确选中的占用删除共同提交，失败全部回滚。
        if slot is not None:
            session.delete(slot)
