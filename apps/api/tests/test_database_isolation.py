"""Verify test isolation and genuine commits across PostgreSQL connections."""

from sqlalchemy import text
from sqlalchemy.engine import Engine, URL


def test_database_and_schema_are_isolated(engine: Engine, test_database_url: URL) -> None:
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    assert test_database_url.database is not None
    assert test_database_url.database.startswith("agent_lab_test_")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == test_database_url.database
        schema = connection.scalar(text("SELECT current_schema()"))
        assert isinstance(schema, str) and schema.startswith("test_")
        assert connection.scalar(text("SELECT count(*) FROM users")) == 0


def test_real_commit_and_rollback_across_physical_connections(engine: Engine) -> None:
    with engine.connect() as writer, engine.connect() as reader:
        assert writer.scalar(text("SELECT pg_backend_pid()")) != reader.scalar(
            text("SELECT pg_backend_pid()")
        )
        writer.execute(text("INSERT INTO users (external_id) VALUES ('committed')"))
        assert reader.scalar(text("SELECT count(*) FROM users")) == 0
        writer.commit()
        reader.rollback()
        assert reader.scalar(text("SELECT count(*) FROM users")) == 1
        writer.execute(text("INSERT INTO users (external_id) VALUES ('rolled-back')"))
        writer.rollback()
        reader.rollback()
        assert reader.execute(text("SELECT external_id FROM users")).scalars().all() == [
            "committed"
        ]
