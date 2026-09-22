"""决策迁移保留历史记录、约束兜底和无损回退边界。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.database import Base
from app.models import FileEditProposal
from tests.migrations.test_database_readiness import migrate
from tests.migrations.test_file_edit_proposal_migration import insert


@pytest.fixture
def historical(empty_engine):
    migrate(empty_engine, "4eb108c473ab")
    with empty_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (external_id) VALUES ('owner')"))
        connection.execute(text("INSERT INTO workspaces (external_id,user_id,name) VALUES ('project',1,'项目')"))
        connection.execute(text("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('task',1,'任务')"))
        insert(connection)
        before = dict(connection.execute(text("SELECT * FROM file_edit_proposals")).mappings().one())
    return empty_engine, before


def downgrade(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "4eb108c473ab")


def test_pending_roundtrip_preserves_every_field(historical):
    engine, before = historical
    migrate(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection, opts={"compare_server_default": True}), Base.metadata) == []
        assert dict(connection.execute(text("SELECT * FROM file_edit_proposals")).mappings().one()) == {**before, "application_status": "idle", "application_token": None}
    downgrade(engine)
    with engine.connect() as connection:
        assert dict(connection.execute(text("SELECT * FROM file_edit_proposals")).mappings().one()) == before
    migrate(engine)


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_downgrade_refuses_to_erase_decisions(historical, decision):
    engine, before = historical
    migrate(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE file_edit_proposals SET status=:status"), {"status": decision})
    with pytest.raises(RuntimeError, match="不能无损回退"):
        downgrade(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "60d32ae695cd"
        assert dict(connection.execute(text("SELECT * FROM file_edit_proposals")).mappings().one()) == {**before, "status": decision, "application_status": "idle", "application_token": None}
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


@pytest.mark.parametrize("status,truncated,valid", [
    ("pending", True, True), ("approved", False, True),
    ("rejected", True, True), ("approved", True, False), ("unknown", False, False),
])
def test_migrated_constraints(historical, status, truncated, valid):
    engine, _ = historical
    migrate(engine)

    def update():
        with engine.begin() as connection:
            connection.execute(text("UPDATE file_edit_proposals SET status=:status, diff_truncated=:truncated"),
                               {"status": status, "truncated": truncated})

    if valid:
        update()
    else:
        with pytest.raises(DBAPIError) as caught:
            update()
        assert caught.value.orig.sqlstate == "23514"
        with engine.connect() as connection:
            assert connection.scalar(select(FileEditProposal.status)) == "pending"
