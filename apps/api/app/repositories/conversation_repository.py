"""会话与消息的 PostgreSQL 持久化实现。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models  # noqa: F401  # 导入模型，使 Base 能收集所有数据表定义。
from app.database import Base, SessionLocal, engine
from app.models import Conversation, Message
from app.repositories.user_repository import get_or_create_user

# 当前尚未接入登录系统；保留一个稳定本地用户，后续由认证后的用户身份替换。
LOCAL_USER_EXTERNAL_ID = "local-demo-user"


def init_db() -> None:
    """首次启动时创建空数据库所需表；已有表不会被 create_all 修改。"""
    Base.metadata.create_all(engine)


def get_or_create_conversation(
    session: Session,
    external_id: str,
) -> Conversation:
    """按 API 的 session_id 查找会话，首次聊天时创建对应会话。"""
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.external_id == external_id,
        ),
    )
    if conversation is not None:
        return conversation

    user = get_or_create_user(session, LOCAL_USER_EXTERNAL_ID)
    conversation = Conversation(
        user_id=user.id,
        external_id=external_id,
    )
    session.add(conversation)
    session.flush()

    return conversation


def load_conversation(session_id: str) -> list[dict[str, str]]:
    """按消息写入顺序读取会话历史，供 Chat Service 重建模型上下文。"""
    with SessionLocal() as session:
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.external_id == session_id,
            ),
        )
        if conversation is None:
            return []

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
    session_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """用一个事务保存完整聊天轮次，避免只写入用户或助手其中一条消息。"""
    with SessionLocal.begin() as session:
        conversation = get_or_create_conversation(session, session_id)
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
