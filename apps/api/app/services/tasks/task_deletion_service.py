"""Task 删除事务：当前只支持没有消息和运行记录的空任务。"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AgentRun, Conversation, Message, Task
from app.repositories.workspace.workspace_repository import (
    WorkspaceNotAccessibleError,
    require_owned_workspace_for_update,
)


class TaskHasHistoryError(Exception):
    """任务已有消息或运行记录，不满足当前删除范围。"""

    code = "task_has_history"

    def __init__(self) -> None:
        super().__init__("当前仅支持删除没有消息和运行记录的空任务")


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
    """拥有一次删除事务；当前拒绝所有已有消息或 Run 的任务。"""

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

        # 必须在会话行锁取得后查询，不能使用加锁前的检查结果。
        # 查到任意一条即可拒绝，不需要加载完整历史。
        message_id = session.scalar(
            select(Message.id)
            .where(Message.conversation_id == conversation.id)
            .limit(1)
        )

        run_id = session.scalar(
            select(AgentRun.id)
            .where(AgentRun.conversation_id == conversation.id)
            .limit(1)
        )

        # 不只检查 running：取消接口写入终态后，协程可能仍在收尾。
        # 本课拒绝所有状态的 Run，也不清理其关联事件。
        if message_id is not None or run_id is not None:
            raise TaskHasHistoryError()

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

        # 两次 DELETE 属于同一事务，中间不能提交。
        # 第二次删除或提交前发生异常时，第一次删除也会回滚。
        session.commit()

    except Exception:
        # 保留数据库异常类型，不伪装成“不存在”或“已有历史”。
        session.rollback()
        raise

    return result
