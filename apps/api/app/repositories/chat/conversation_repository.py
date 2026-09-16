"""会话与消息的 PostgreSQL 持久化实现。"""

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.models import Conversation, Message, Task, Workspace
from app.config import settings


def load_conversation(
    *,
    user_id: int,
    session_id: str,
) -> list[dict[str, str]]:
    """读取当前用户拥有的会话历史。"""
    with SessionLocal() as session:
        conversation = require_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
        )

        messages = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id),
        ).all()

        return [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]


def save_conversation_turn(
    *,
    user_id: int,
    session_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """检查所有权后，用一个事务保存完整聊天轮次。"""
    with SessionLocal.begin() as session:
        conversation = require_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
        )

        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=user_content,
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=assistant_content,
                ),
            ],
        )


class ConversationNotAccessibleError(Exception):
    """会话不存在，或不属于当前用户。"""

    code = "conversation_not_accessible"

    def __init__(self) -> None:
        super().__init__("会话不存在或不可访问")


def require_owned_conversation(
    session: Session,
    *,
    user_id: int,
    session_id: str,
    for_update: bool = False,
) -> Conversation:
    """读取可访问会话；本地模式额外检查 Task 和 Workspace 归属。"""

    statement = select(Conversation).where(
        Conversation.external_id == session_id,
        Conversation.user_id == user_id,
    )

    if settings.app_mode == "local":
        # 本地产品只能在已创建的任务中聊天。
        # INNER JOIN 同时拒绝没有 Task 的旧会话和缺失关联的数据。
        statement = (
            statement
            .join(
                Task,
                Conversation.task_id == Task.id,
            )
            .join(
                Workspace,
                Task.workspace_id == Workspace.id,
            )
            .where(
                Workspace.user_id == user_id,
            )
        )

    if for_update:
        # 只锁会话行，保护后续 Run 写入与任务删除之间的并发边界。
        # 锁由调用方事务释放，此处不 commit，也不跨模型调用持锁。
        statement = (
            statement
            .with_for_update(of=Conversation)
            .execution_options(populate_existing=True)
        )

    conversation = session.scalar(statement)

    if conversation is None:
        # 不区分不存在、已删除、无任务或归属错误，沿用统一安全异常。
        raise ConversationNotAccessibleError()

    return conversation


def get_or_create_owned_conversation(
    session: Session,
    *,
    user_id: int,
    session_id: str,
) -> Conversation:
    """本地模式只读取已有任务会话；账号模式保留既有创建行为。"""

    if settings.app_mode == "local":
        # 必须在构造和执行 INSERT 之前返回。
        # 不存在的会话不能被迟到请求重新创建。
        return require_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
            for_update=True,
        )

    # 账号模式保留历史兼容行为。
    # 标识冲突时不修改所有者，随后重新检查归属。
    statement = (
        insert(Conversation)
        .values(
            external_id=session_id,
            user_id=user_id,
        )
        .on_conflict_do_nothing(
            index_elements=[Conversation.external_id],
        )
    )

    session.execute(statement)

    return require_owned_conversation(
        session,
        user_id=user_id,
        session_id=session_id,
    )


def ensure_owned_conversation(
    *,
    user_id: int,
    session_id: str,
) -> None:
    """校验聊天会话；只有账号模式允许在此隐式创建。"""

    # 本地模式在短事务中检查并锁定已有任务会话。
    # 返回前事务结束，不把锁带入缓存处理或模型等待。
    with SessionLocal.begin() as session:
        get_or_create_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
        )
