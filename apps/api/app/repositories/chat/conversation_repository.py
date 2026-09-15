"""会话与消息的 PostgreSQL 持久化实现。"""

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from app import models  # noqa: F401  # 导入模型，使 Base 能收集所有数据表定义。
from app.database import Base, SessionLocal, engine
from app.models import Conversation, Message


def init_db() -> None:
    """首次启动时创建空数据库所需表；已有表不会被 create_all 修改。"""
    Base.metadata.create_all(engine)


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
) -> Conversation:
    """读取当前用户拥有的会话，不创建记录。"""
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.external_id == session_id,
            Conversation.user_id == user_id,
        )
    )

    if conversation is None:
        raise ConversationNotAccessibleError()

    return conversation


def get_or_create_owned_conversation(
    session: Session,
    *,
    user_id: int,
    session_id: str,
) -> Conversation:
    """创建当前用户的会话；标识已存在时重新检查所有权。"""
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
    """确保会话属于当前用户；新会话成功创建后提交。"""
    with SessionLocal.begin() as session:
        get_or_create_owned_conversation(
            session,
            user_id=user_id,
            session_id=session_id,
        )
