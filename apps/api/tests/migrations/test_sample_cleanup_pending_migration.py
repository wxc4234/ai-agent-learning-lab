"""真实PostgreSQL迁移保留旧来源，并拒绝丢失未完成的清理待办。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app.database import Base
from tests.migrations.test_database_readiness import migrate


@pytest.fixture
def upgraded(empty_engine):
    migrate(empty_engine, "71c43e9a8f02")
    with empty_engine.begin() as connection:
        connection.execute(text("INSERT INTO users(external_id) VALUES ('owner')"))
        connection.execute(text(
            "INSERT INTO workspaces(external_id,user_id,name,root_path) "
            "VALUES ('workspace',1,'sample','/sample')"
        ))
        connection.execute(text(
            "INSERT INTO tasks(external_id,workspace_id,title) VALUES ('task',1,'task')"
        ))
        connection.execute(text(
            "INSERT INTO workspace_sample_origins(workspace_id,task_id,root_path) "
            "VALUES (1,1,'/sample')"
        ))
    migrate(empty_engine)
    return empty_engine


def downgrade(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "71c43e9a8f02")


def test_upgrade_keeps_existing_origin_active_and_matches_model(upgraded):
    with upgraded.connect() as connection:
        assert connection.scalar(text(
            "SELECT lifecycle_state FROM workspace_sample_origins"
        )) == "active"
        assert compare_metadata(
            MigrationContext.configure(connection, opts={"compare_server_default": True}),
            Base.metadata,
        ) == []
    downgrade(upgraded)
    with upgraded.connect() as connection:
        assert "lifecycle_state" not in {
            column["name"] for column in inspect(connection).get_columns("workspace_sample_origins")
        }
        assert connection.scalar(text(
            "SELECT root_path FROM workspace_sample_origins"
        )) == "/sample"


def test_downgrade_refuses_pending_evidence(upgraded):
    with upgraded.begin() as connection:
        connection.execute(text(
            "UPDATE workspace_sample_origins SET lifecycle_state = 'cleanup_pending'"
        ))
    with pytest.raises(RuntimeError, match="不能无损回退"):
        downgrade(upgraded)
    with upgraded.connect() as connection:
        assert connection.scalar(text(
            "SELECT lifecycle_state FROM workspace_sample_origins"
        )) == "cleanup_pending"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "93e4c2d7a10b"


def test_database_rejects_unknown_cleanup_state(upgraded):
    with pytest.raises(DBAPIError) as caught, upgraded.begin() as connection:
        connection.execute(text(
            "UPDATE workspace_sample_origins SET lifecycle_state = 'unknown'"
        ))
    assert caught.value.orig.sqlstate == "23514"
