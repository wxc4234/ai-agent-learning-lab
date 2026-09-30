"""向量迁移真实升级、DDL回滚和无损回退；共享扩展不能被降级删除。"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import event, inspect, text

from app.database import Base
from tests.migrations.test_database_readiness import migrate


PREVIOUS = "d7c8a61be54f"
CURRENT = "6f4c2b8d901a"


def downgrade(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, PREVIOUS)


def test_real_upgrade_metadata_and_empty_downgrade_keep_shared_extension(empty_engine):
    migrate(empty_engine, PREVIOUS)
    with empty_engine.begin() as connection:
        connection.execute(text("INSERT INTO users(external_id) VALUES ('legacy')"))
        connection.execute(
            text(
                "INSERT INTO workspaces(external_id,user_id,name,root_path) VALUES ('legacy',1,'legacy','/preserved')"
            )
        )
    migrate(empty_engine)
    with empty_engine.connect() as connection:
        assert (
            compare_metadata(
                MigrationContext.configure(
                    connection, opts={"compare_server_default": True}
                ),
                Base.metadata,
            )
            == []
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == CURRENT
        )
        assert (
            connection.scalar(text("SELECT root_path FROM workspaces")) == "/preserved"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'"
                )
            )
            == "public"
        )
    downgrade(empty_engine)
    with empty_engine.connect() as connection:
        assert "code_embedding_vectors" not in inspect(connection).get_table_names()
        assert (
            connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")
            )
            is True
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == PREVIOUS
        )
    migrate(empty_engine)


def test_nonempty_space_refuses_lossy_downgrade(empty_engine):
    migrate(empty_engine)
    with empty_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO code_embedding_spaces(id,dimensions,requested_model,response_model) VALUES (repeat('a',64),3,'fixture','fixture-v1')"
            )
        )
    with pytest.raises(
        RuntimeError, match="refusing to discard code embedding snapshots"
    ):
        downgrade(empty_engine)
    with empty_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == CURRENT
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM code_embedding_spaces")) == 1
        )


def test_late_ddl_failure_rolls_back_new_tables_and_version(empty_engine):
    migrate(empty_engine, PREVIOUS)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("CREATE TABLE code_embedding_vectors"):
            raise RuntimeError("injected migration failure")

    event.listen(empty_engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected migration failure"):
            migrate(empty_engine)
    finally:
        event.remove(empty_engine, "before_cursor_execute", fail)
    with empty_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == PREVIOUS
        )
        assert not any(
            name.startswith("code_embedding_")
            for name in inspect(connection).get_table_names()
        )
