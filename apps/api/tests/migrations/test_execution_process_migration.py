"""升级旧占用不能捏造进程身份。"""

from sqlalchemy import text
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.database import Base
from tests.migrations.test_database_readiness import migrate


def test_legacy_slot_preserved_without_guessed_identity(empty_engine):
    migrate(empty_engine, '1b8c75f140de')
    with empty_engine.begin() as conn:
        conn.execute(text("INSERT INTO users(external_id) VALUES ('legacy')"))
        conn.execute(text("INSERT INTO conversations(user_id,external_id) VALUES (1,'legacy')"))
        conn.execute(text("INSERT INTO conversation_execution_slots(conversation_id,owner_token) VALUES (1,:token)"), {'token': 'a' * 32})
    migrate(empty_engine)
    with empty_engine.connect() as conn:
        row = conn.execute(text('SELECT owner_token,owner_host_id,owner_pid FROM conversation_execution_slots')).one()
        assert tuple(row) == ('a' * 32, None, None)
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
