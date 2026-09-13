"""PostgreSQL migration tests in the dedicated temporary test database."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

@pytest.mark.parametrize("state", ["legacy", "fresh", "both", "invalid", "duplicates"])
def test_normalize_conversation_index(state, empty_engine):
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/b62d19f804ae_normalize_conversation_unique_index.py"
    )
    spec = importlib.util.spec_from_file_location("index_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with empty_engine.connect() as conn:
        transaction = conn.begin()
        try:
            schema = "qa_index_" + uuid4().hex
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            conn.execute(
                sa.text(
                    "CREATE TABLE conversations (id INTEGER PRIMARY KEY, external_id VARCHAR(100) NOT NULL)"
                )
            )
            conn.execute(sa.text("INSERT INTO conversations VALUES (1, 'existing')"))
            if state in {"legacy", "both"}:
                conn.execute(
                    sa.text(
                        "ALTER TABLE conversations ADD CONSTRAINT uq_conversations_external_id UNIQUE (external_id)"
                    )
                )
            if state in {"fresh", "both", "invalid"}:
                unique = "" if state == "invalid" else "UNIQUE"
                conn.execute(
                    sa.text(
                        f"CREATE {unique} INDEX ix_conversations_external_id ON conversations (external_id)"
                    )
                )
            if state == "duplicates":
                conn.execute(
                    sa.text("INSERT INTO conversations VALUES (2, 'existing')")
                )
            with Operations.context(MigrationContext.configure(conn)):
                if state in {"invalid", "duplicates"}:
                    error = RuntimeError if state == "invalid" else IntegrityError
                    with pytest.raises(error), conn.begin_nested():
                        migration.upgrade()
                    assert conn.scalar(
                        sa.text("SELECT COUNT(*) FROM conversations")
                    ) == (2 if state == "duplicates" else 1)
                    return
                migration.upgrade()
                migration.upgrade()
                migration.downgrade()
                indexes = sa.inspect(conn).get_indexes("conversations")
                assert any(
                    i["name"] == "ix_conversations_external_id" and i["unique"]
                    for i in indexes
                )
                assert not sa.inspect(conn).get_unique_constraints("conversations")
                assert conn.execute(sa.text("SELECT * FROM conversations")).all() == [
                    (1, "existing")
                ]
                with pytest.raises(IntegrityError), conn.begin_nested():
                    conn.execute(
                        sa.text("INSERT INTO conversations VALUES (2, 'existing')")
                    )
        finally:
            transaction.rollback()
