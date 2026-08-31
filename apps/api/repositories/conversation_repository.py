"""会话消息的 SQLite 持久化实现。"""

import sqlite3

from config import APP_DIR

# 数据文件属于应用目录，而非当前模块目录；移动 Repository 文件也不会丢失历史数据。
DB_PATH = APP_DIR / "chat.db"


def init_db():
    """在服务启动时幂等创建消息表，新电脑首次运行也可直接启动。"""
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)

        connection.commit()


def save_conversation_turn(
    session_id: str,
    user_content: str,
    assistant_content: str,
):
    """用一次事务连续保存用户消息和助手回复，避免只写入半轮对话。"""
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO messages (session_id, role, content)
            VALUES (?, ?, ?)
            """,
            (session_id, "user", user_content),
        )

        connection.execute(
            """
            INSERT INTO messages (session_id, role, content)
            VALUES (?, ?, ?)
            """,
            (session_id, "assistant", assistant_content),
        )

        connection.commit()


def load_conversation(session_id: str) -> list[dict[str, str]]:
    """按写入顺序恢复会话，供聊天 Service 重新构建模型上下文。"""
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            SELECT role, content
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            # 参数化查询避免把用户输入拼进 SQL，防止注入并正确处理特殊字符。
            (session_id,),
        )
        rows = cursor.fetchall()

    return [
        {
            "role": role,
            "content": content,
        }
        for role, content in rows
    ]
