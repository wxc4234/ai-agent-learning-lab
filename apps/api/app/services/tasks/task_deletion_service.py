"""Task 删除事务：拒绝执行占用及未结束运行，原子清理任务历史。"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.repositories.workspace.proposal_application_guard import require_no_active_proposal_application

from app.models import AgentRun, AgentRunEvent, Conversation, ConversationExecutionSlot, Message, Task
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    require_owned_workspace_for_update,
)
from app.services.runtime.execution.conversation_execution_service import ConversationBusyError


class TaskRunUnsettledError(Exception):
    """运行状态未确认结束，拒绝删除。"""

    code = "task_run_unsettled"

    def __init__(self) -> None:
        super().__init__("存在未确认结束的运行，暂不能删除")


@dataclass(frozen=True)
class TaskDeletionResult:
    """删除结果只保存公开标识，不依赖已删除的 ORM 对象。"""

    workspace_id: str
    task_id: str
    conversation_id: str


def delete_workspace_task(
    session: Session,
    *,
    user_id: int,
    workspace_id: str,
    task_id: str,
) -> TaskDeletionResult:
    """拥有一次删除事务；在会话锁内验证执行停止，再原子删除历史。"""

    # 与创建服务保持一致：不能接管或回滚调用方已有事务。
    if session.in_transaction():
        raise RuntimeError("Task 删除服务需要无活动事务的 Session")

    try:
        # 身份来自可信调用方。项目标识和所有者必须同时匹配。
        # 采用项目 → 任务 → 会话的锁顺序，锁一直保持到事务结束。
        workspace = require_owned_workspace_for_update(
            session=session,
            user_id=user_id,
            workspace_id=workspace_id,
        )

        task = session.scalar(
            select(Task)
            .where(
                Task.external_id == task_id,
                Task.workspace_id == workspace.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        if task is None:
            # 沿用已有任务详情的拒绝方式，不区分不存在或错误项目。
            raise WorkspaceNotAccessibleError()

        conversation = session.scalar(
            select(Conversation)
            .where(
                Conversation.task_id == task.id,
                Conversation.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        if conversation is None:
            # 正常创建事务会同时创建会话。
            # 缺失会话或归属错配时拒绝，不借删除服务修复异常数据。
            raise WorkspaceNotAccessibleError()

        # 与应用领取串行；在任何DELETE之前拒绝未结束的文件副作用。
        require_no_active_proposal_application(session, workspace_id=workspace.id, task_id=task.id)

        # 获取/释放占用也先锁会话；在同一行锁内检查才能与它们串行。
        # 占用可能早于 Run 创建，也可能在 Run 终态后继续收尾。
        # 只读取是否存在，不读取 token，不按时间推断执行已停止。
        occupied_conversation_id = session.scalar(
            select(ConversationExecutionSlot.conversation_id)
            .where(ConversationExecutionSlot.conversation_id == conversation.id)
        )
        if occupied_conversation_id is not None:
            # 交给统一异常分支回滚本次事务，保留任务和原占用。
            raise ConversationBusyError()

        # 锁定 Run，与取消/终态写入串行；未知状态与缺结束时间均保守拒绝。
        runs = session.scalars(
            select(AgentRun).where(AgentRun.conversation_id == conversation.id)
            .order_by(AgentRun.id).with_for_update()
        ).all()
        if any(run.status not in {'done', 'error', 'aborted'} or run.finished_at is None for run in runs):
            raise TaskRunUnsettledError()

        # 按外键依赖清理，所有历史与 Task 共用同一个提交边界。
        run_ids = select(AgentRun.id).where(AgentRun.conversation_id == conversation.id)
        session.execute(delete(AgentRunEvent).where(AgentRunEvent.run_id.in_(run_ids)))
        session.execute(delete(AgentRun).where(AgentRun.conversation_id == conversation.id))
        session.execute(delete(Message).where(Message.conversation_id == conversation.id))

        # 提交前复制公开字段，避免删除后或提交后读取 ORM 状态。
        result = TaskDeletionResult(
            workspace_id=workspace.external_id,
            task_id=task.external_id,
            conversation_id=conversation.external_id,
        )

        # 当前没有 ON DELETE CASCADE，因此显式先删引用 Task 的会话。
        # 使用条件 DELETE，不依赖 ORM relationship 自动置空或级联。
        session.execute(
            delete(Conversation)
            .where(Conversation.id == conversation.id)
            .execution_options(synchronize_session=False)
        )

        session.execute(
            delete(Task)
            .where(Task.id == task.id)
            .execution_options(synchronize_session=False)
        )

        # 历史与任务的全部 DELETE 属于同一事务，中间不能提交。
        # 第二次删除或提交前发生异常时，第一次删除也会回滚。
        session.commit()

    except Exception:
        # 保留数据库异常类型，不伪装成“不存在”或“已有历史”。
        session.rollback()
        raise

    return result
