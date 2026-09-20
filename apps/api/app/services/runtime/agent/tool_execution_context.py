"""从已授权会话构造工具上下文，不访问文件系统或修改数据库。"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Conversation, Task, Workspace
from app.repositories.chat.conversation_repository import (
    ConversationNotAccessibleError,
)
from app.tools.context import ToolExecutionContext


def load_tool_execution_context(
    *,
    user_id: int,
    conversation_id: str,
) -> ToolExecutionContext:
    """通过会话、任务和项目的完整归属关系确定工具执行范围。"""

    # user_id 必须来自服务端认证结果。
    # conversation_id 使用当前聊天请求的会话标识，并在此重新授权。
    # 不接受独立的 workspace_id、task_id 或 root_path 输入。
    statement = (
        select(
            Conversation.external_id.label("conversation_id"),
            Task.external_id.label("task_id"),
            Workspace.external_id.label("workspace_id"),
        )
        .select_from(Conversation)
        .join(
            Task,
            Conversation.task_id == Task.id,
        )
        .join(
            Workspace,
            Task.workspace_id == Workspace.id,
        )
        .where(
            Conversation.external_id == conversation_id,
            Conversation.user_id == user_id,
            Workspace.user_id == user_id,
        )
    )

    with SessionLocal() as session:
        # INNER JOIN 同时拒绝没有 Task 的历史会话和缺失的关联。
        # 显式检查会话与项目所有者，不依赖 APP_MODE 的条件分支。
        row = session.execute(statement).mappings().one_or_none()

        if row is None:
            # 不区分不存在、他人会话、归属不一致或关联缺失，
            # 避免通过错误信息暴露资源关系。
            raise ConversationNotAccessibleError()

        # 在 Session 关闭前复制普通字段。
        # 不向 Runtime 传递 ORM 对象，也不让后台工具线程复用 Session。
        context = ToolExecutionContext(
            user_id=user_id,
            conversation_id=row["conversation_id"],
            workspace_id=row["workspace_id"],
            task_id=row["task_id"],
        )

    # 只有 SELECT，无需 commit；离开 with 后事务和连接已释放。
    # 后续模型等待、文件操作和工具线程均不占用本次数据库事务。
    return context
