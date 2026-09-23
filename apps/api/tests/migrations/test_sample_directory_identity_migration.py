"""真实 PostgreSQL 迁移只为新样例保存身份，不为旧来源伪造证据。"""

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
    migrate(empty_engine, "82d14f5b09ad")
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
        command.downgrade(config, "82d14f5b09ad")


def test_upgrade_keeps_legacy_identity_unknown_and_matches_model(upgraded):
    with upgraded.connect() as connection:
        assert connection.execute(text(
            "SELECT parent_dev,parent_ino,root_dev,root_ino "
            "FROM workspace_sample_origins"
        )).one() == (None, None, None, None)
        assert compare_metadata(
            MigrationContext.configure(connection, opts={"compare_server_default": True}),
            Base.metadata,
        ) == []

    downgrade(upgraded)
    with upgraded.connect() as connection:
        names = {
            column["name"] for column in inspect(connection).get_columns("workspace_sample_origins")
        }
        assert names.isdisjoint({"parent_dev", "parent_ino", "root_dev", "root_ino"})
        assert connection.scalar(text(
            "SELECT root_path FROM workspace_sample_origins"
        )) == "/sample"


def test_complete_identity_is_stored_without_changing_legacy_origin(upgraded):
    with upgraded.begin() as connection:
        connection.execute(text(
            "UPDATE workspace_sample_origins SET "
            "parent_dev=11,parent_ino=22,root_dev=33,root_ino=44"
        ))
    with upgraded.connect() as connection:
        assert connection.execute(text(
            "SELECT parent_dev,parent_ino,root_dev,root_ino "
            "FROM workspace_sample_origins"
        )).one() == (11, 22, 33, 44)


@pytest.mark.parametrize("identity", [
    (11, None, 33, 44),
    (11, 22, None, 44),
    (11, 0, 33, 44),
    (-1, 22, 33, 44),
])
def test_database_rejects_partial_or_invalid_identity(upgraded, identity):
    with pytest.raises(DBAPIError) as caught, upgraded.begin() as connection:
        connection.execute(text(
            "UPDATE workspace_sample_origins SET "
            "parent_dev=:parent_dev,parent_ino=:parent_ino,"
            "root_dev=:root_dev,root_ino=:root_ino"
        ), dict(zip(
            ("parent_dev", "parent_ino", "root_dev", "root_ino"),
            identity,
            strict=True,
        )))
    assert caught.value.orig.sqlstate == "23514"
    with upgraded.connect() as connection:
        assert connection.execute(text(
            "SELECT parent_dev,parent_ino,root_dev,root_ino "
            "FROM workspace_sample_origins"
        )).one() == (None, None, None, None)


def test_downgrade_refuses_to_erase_persisted_identity(upgraded):
    with upgraded.begin() as connection:
        connection.execute(text(
            "UPDATE workspace_sample_origins SET "
            "parent_dev=11,parent_ino=22,root_dev=33,root_ino=44"
        ))
    with pytest.raises(RuntimeError, match="不能无损回退"):
        downgrade(upgraded)
    with upgraded.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "93e4c2d7a10b"
        assert connection.execute(text(
            "SELECT parent_dev,parent_ino,root_dev,root_ino "
            "FROM workspace_sample_origins"
        )).one() == (11, 22, 33, 44)
