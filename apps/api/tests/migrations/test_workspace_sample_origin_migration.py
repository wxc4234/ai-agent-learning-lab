"""真实迁移只记录新样例，约束归属并拒绝丢失持久来源。"""

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
    migrate(empty_engine, "60d32ae695cd")
    with empty_engine.begin() as connection:
        connection.execute(text("INSERT INTO users(external_id) VALUES ('owner')"))
        connection.execute(text(
            "INSERT INTO workspaces(external_id,user_id,name,root_path) "
            "VALUES ('workspace',1,'ordinary','/ordinary')"
        ))
        connection.execute(text(
            "INSERT INTO tasks(external_id,workspace_id,title) VALUES ('task',1,'task')"
        ))
    migrate(empty_engine)
    return empty_engine


def downgrade(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "60d32ae695cd")


def test_upgrade_preserves_ordinary_directory_and_empty_origin(upgraded):
    with upgraded.connect() as connection:
        assert connection.scalar(text("SELECT root_path FROM workspaces")) == "/ordinary"
        assert connection.scalar(text("SELECT count(*) FROM workspace_sample_origins")) == 0
        assert compare_metadata(
            MigrationContext.configure(connection, opts={"compare_server_default": True}),
            Base.metadata,
        ) == []
    downgrade(upgraded)
    with upgraded.connect() as connection:
        assert "workspace_sample_origins" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT root_path FROM workspaces")) == "/ordinary"


def test_downgrade_refuses_to_erase_origin(upgraded):
    with upgraded.begin() as connection:
        connection.execute(text(
            "INSERT INTO workspace_sample_origins(workspace_id,task_id,root_path) "
            "VALUES (1,1,'/sample')"
        ))
    with pytest.raises(RuntimeError, match="不能无损回退"):
        downgrade(upgraded)
    with upgraded.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM workspace_sample_origins")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "93e4c2d7a10b"


@pytest.mark.parametrize("workspace_id,task_id,root_path,state", [
    (1, 1, "", "23514"),
    (1, 9999, "/sample", "23503"),
    (9999, 1, "/sample", "23503"),
])
def test_database_rejects_invalid_origin(upgraded, workspace_id, task_id, root_path, state):
    with pytest.raises(DBAPIError) as caught, upgraded.begin() as connection:
        connection.execute(text(
            "INSERT INTO workspace_sample_origins(workspace_id,task_id,root_path) "
            "VALUES (:workspace_id,:task_id,:root_path)"
        ), {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "root_path": root_path,
        })
    assert caught.value.orig.sqlstate == state
