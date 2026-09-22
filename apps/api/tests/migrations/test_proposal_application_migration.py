"""真实迁移保留审批、约束执行占用并拒绝有损降级。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database import Base
from tests.migrations.test_database_readiness import migrate
from tests.migrations.test_file_edit_proposal_migration import insert


@pytest.fixture
def old(empty_engine):
    migrate(empty_engine, '5fc219d584bc')
    with empty_engine.begin() as c:
        c.execute(text("INSERT INTO users(external_id) VALUES ('owner')"))
        c.execute(text("INSERT INTO workspaces(external_id,user_id,name) VALUES ('w',1,'w')"))
        c.execute(text("INSERT INTO tasks(external_id,workspace_id,title) VALUES ('t',1,'t')"))
        insert(c, status='approved')
        before = dict(c.execute(text('SELECT * FROM file_edit_proposals')).mappings().one())
    migrate(empty_engine)
    return empty_engine, before


def down(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with engine.begin() as c:
        config.attributes['connection'] = c
        command.downgrade(config, '5fc219d584bc')


def test_roundtrip_preserves_approved_history(old):
    engine, before = old
    with engine.connect() as c:
        assert dict(c.execute(text('SELECT * FROM file_edit_proposals')).mappings().one()) == {
            **before, 'application_status': 'idle', 'application_token': None}
        assert compare_metadata(MigrationContext.configure(c, opts={'compare_server_default': True}), Base.metadata) == []
    down(engine)
    with engine.connect() as c:
        assert dict(c.execute(text('SELECT * FROM file_edit_proposals')).mappings().one()) == before


@pytest.mark.parametrize('status', ['running', 'applied', 'not_applied', 'uncertain'])
def test_no_lossy_downgrade(old, status):
    engine, _ = old
    with engine.begin() as c:
        c.execute(text('UPDATE file_edit_proposals SET application_status=:state, application_token=:token'),
                  {'state': status, 'token': 'a' * 32})
    with pytest.raises(RuntimeError, match='不能无损回退'):
        down(engine)
    with engine.connect() as c:
        assert c.scalar(text('SELECT application_status FROM file_edit_proposals')) == status
        assert c.scalar(text('SELECT version_num FROM alembic_version')) == '60d32ae695cd'


@pytest.mark.parametrize('state,token,decision', [
    ('bad', None, 'approved'), ('running', None, 'approved'), ('idle', 'a' * 32, 'approved'),
    ('running', 'bad', 'approved'), ('running', 'a' * 32, 'pending'), ('applied', 'a' * 32, 'rejected'),
])
def test_database_constraints(old, state, token, decision):
    with pytest.raises(DBAPIError) as caught, old[0].begin() as c:
        c.execute(text('UPDATE file_edit_proposals SET application_status=:s, application_token=:t, status=:d'),
                  {'s': state, 't': token, 'd': decision})
    assert caught.value.orig.sqlstate == '23514'
