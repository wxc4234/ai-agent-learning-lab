"""一次性将旧 SQLite 聊天历史迁移到 PostgreSQL。"""

import sqlite3

from app.config import API_DIR
from app.database import SessionLocal
from app.models import Conversation, Message
from app.repositories.auth.user_repository import get_or_create_user
from sqlalchemy import select

# SQLite 时代没有真实登录系统；历史数据统一归属本地演示用户，之后可迁移到认证用户。
LEGACY_USER_EXTERNAL_ID = "local-demo-user"
LEGACY_DB_PATH = API_DIR / "chat.db"


def load_legacy_messages() -> dict[str, list[tuple[str, str]]]:
    """按旧 session_id 聚合消息，并保留 SQLite 的原始写入顺序。"""
    messages_by_session: dict[str, list[tuple[str, str]]] = {}

    with sqlite3.connect(LEGACY_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT session_id, role, content
            FROM messages
            ORDER BY id ASC
            """,
        ).fetchall()

    for session_id, role, content in rows:
        messages_by_session.setdefault(session_id, []).append((role, content))

    return messages_by_session


def migrate() -> tuple[int, int, int]:
    """迁移历史会话；已有目标消息的会话会跳过，从而可安全重试。"""
    messages_by_session = load_legacy_messages()
    migrated_sessions = 0
    migrated_messages = 0
    skipped_sessions = 0

    # 一个事务覆盖所有写入：任何一步失败时，不会只导入半个会话。
    with SessionLocal.begin() as session:
        user = get_or_create_user(session, LEGACY_USER_EXTERNAL_ID)

        for external_id, legacy_messages in messages_by_session.items():
            conversation = session.scalar(
                select(Conversation).where(
                    Conversation.external_id == external_id,
                ),
            )

            if conversation is None:
                conversation = Conversation(
                    user_id=user.id,
                    external_id=external_id,
                    title=f"导入会话：{external_id}",
                )
                session.add(conversation)
                session.flush()
            else:
                # 会话已有消息意味着之前已经完整导入；跳过可避免重复历史记录。
                existing_message = session.scalar(
                    select(Message.id)
                    .where(Message.conversation_id == conversation.id)
                    .limit(1),
                )
                if existing_message is not None:
                    skipped_sessions += 1
                    continue

            session.add_all(
                [
                    Message(
                        conversation_id=conversation.id,
                        role=role,
                        content=content,
                    )
                    for role, content in legacy_messages
                ],
            )
            migrated_sessions += 1
            migrated_messages += len(legacy_messages)

    return migrated_sessions, migrated_messages, skipped_sessions


def main() -> None:
    """执行迁移，并输出可用于人工核对的汇总。"""
    migrated_sessions, migrated_messages, skipped_sessions = migrate()
    print(f"迁移会话数: {migrated_sessions}")
    print(f"迁移消息数: {migrated_messages}")
    print(f"跳过已迁移会话数: {skipped_sessions}")


if __name__ == "__main__":
    main()
