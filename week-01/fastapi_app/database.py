import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "chat.db"


def init_db():
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
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            SELECT role, content
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
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
