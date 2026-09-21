"""真实迁移、历史数据保留与提案数据库边界。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.database import Base
from app.models import FileEditProposal
from tests.migrations.test_database_readiness import migrate
from tests.migrations.test_task_creation_request_migration import snapshot


@pytest.fixture
def migrated(empty_engine):
    migrate(empty_engine, "3da097b362fa")
    with empty_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (external_id) VALUES ('owner')"))
        connection.execute(text("INSERT INTO workspaces (external_id,user_id,name) VALUES ('project',1,'项目')"))
        connection.execute(text("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('task',1,'旧任务')"))
        old = MetaData()
        old.reflect(connection)
        before = snapshot(connection, old)
    migrate(empty_engine)
    return empty_engine, old, before


def insert(connection, **overrides):
    values = {
        "external_id": "a" * 32, "task_id": 1, "bound_root": "/private/project",
        "relative_path": "src/file.txt", "baseline_sha256": "b" * 64,
        "proposed_sha256": "c" * 64, "proposed_content": "", "diff": "review", "diff_truncated": False,
    }
    connection.execute(FileEditProposal.__table__.insert().values(**{**values, **overrides}))


def test_round_trip_and_metadata(migrated):
    engine, old, before = migrated
    with engine.begin() as connection:
        assert compare_metadata(MigrationContext.configure(connection, opts={"compare_server_default": True}), Base.metadata) == []
        assert snapshot(connection, old) == before
        insert(connection)
        assert connection.execute(select(FileEditProposal.status)).scalar_one() == "pending"
        fk = inspect(connection).get_foreign_keys("file_edit_proposals")
        assert fk[0]["options"]["ondelete"] == "CASCADE"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "3da097b362fa")
        assert "file_edit_proposals" not in inspect(connection).get_table_names()
        assert snapshot(connection, old) == before
    migrate(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.execute(text("SELECT count(*) FROM file_edit_proposals")).scalar_one() == 0


@pytest.mark.parametrize("overrides,state", [
    ({"status": "approved"}, "23514"), ({"status": None}, "23502"),
    ({"bound_root": ""}, "23514"), ({"bound_root": "x" * 4097}, "23514"),
    ({"relative_path": ""}, "23514"), ({"relative_path": "x" * 4097}, "22001"),
    ({"baseline_sha256": "B" * 64}, "23514"), ({"baseline_sha256": "b" * 63}, "23514"),
    ({"proposed_sha256": "g" * 64}, "23514"), ({"proposed_sha256": None}, "23502"),
    ({"proposed_content": "中" * 87382}, "23514"), ({"proposed_content": None}, "23502"),
    ({"diff": ""}, "23514"), ({"diff": "x" * 16385}, "23514"),
    ({"diff_truncated": None}, "23502"), ({"task_id": None}, "23502"),
    ({"task_id": 999999}, "23503"),
])
def test_constraints_on_migrated_schema(migrated, overrides, state):
    with pytest.raises(DBAPIError) as caught, migrated[0].begin() as connection:
        insert(connection, **overrides)
    assert caught.value.orig.sqlstate == state


def test_limits_uniqueness_and_cascade(migrated):
    engine = migrated[0]
    with engine.begin() as connection:
        insert(connection, proposed_content="中" * 87381 + "x", diff="中" * 16384,
               relative_path="x" * 4096, bound_root="x" * 4096)
    with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
        insert(connection)
    assert caught.value.orig.sqlstate == "23505"
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM tasks WHERE id=1"))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM file_edit_proposals")).scalar_one() == 0
