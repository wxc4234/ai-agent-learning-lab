"""Round-trip new migration only in a dedicated PostgreSQL test schema."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.database import Base


def test_migration_preserves_history_and_matches_models(empty_engine: Engine):
    metadata = Base.metadata
    old_tables = [table for table in metadata.sorted_tables if table.name != "login_sessions"]
    metadata.create_all(empty_engine, tables=old_tables)
    with empty_engine.begin() as conn:
        conn.execute(metadata.tables["users"].insert().values(id=1, external_id="legacy"))
        conn.execute(metadata.tables["conversations"].insert().values(id=1, user_id=1, external_id="chat"))
        conn.execute(metadata.tables["messages"].insert().values(id=1, conversation_id=1, role="user", content="历史中文消息"))
        conn.execute(metadata.tables["agent_runs"].insert().values(id=1, conversation_id=1, status="done"))
        conn.execute(metadata.tables["agent_run_events"].insert().values(id=1, run_id=1, event_type="DONE", payload={"history": True}))
    path = Path(__file__).resolve().parents[1] / "migrations/versions/c83f20a915bd_add_login_sessions.py"
    spec = importlib.util.spec_from_file_location("login_session_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with empty_engine.begin() as conn:
        before = {table.name: conn.execute(select(table)).all() for table in old_tables}
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            migration.upgrade()
            assert compare_metadata(context, metadata) == []
            now = datetime.now(UTC)
            conn.execute(metadata.tables["login_sessions"].insert().values(
                user_id=1, token_hash="a" * 64, created_at=now, expires_at=now + timedelta(hours=1),
            ))
            migration.downgrade()
            migration.upgrade()
            assert compare_metadata(context, metadata) == []
            assert conn.execute(select(metadata.tables["login_sessions"])).all() == []
            after = {table.name: conn.execute(select(table)).all() for table in old_tables}
            assert after == before
