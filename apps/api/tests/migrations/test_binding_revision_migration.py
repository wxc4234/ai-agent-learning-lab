"""真实PostgreSQL迁移：旧路径保留、版本初始化、约束与防有损回退。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import Base
from tests.migrations.test_database_readiness import migrate


@pytest.fixture
def upgraded(empty_engine):
    migrate(empty_engine, '93e4c2d7a10b')
    with empty_engine.begin() as connection:
        connection.execute(text("INSERT INTO users(external_id) VALUES ('owner')"))
        connection.execute(text("INSERT INTO workspaces(external_id,user_id,name,root_path) VALUES "
                                "('bound',1,'bound','/original'), ('empty',1,'empty',NULL)"))
    migrate(empty_engine)
    return empty_engine


def downgrade(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '93e4c2d7a10b')


def test_legacy_paths_preserved_and_metadata_matches(upgraded):
    with upgraded.connect() as connection:
        assert connection.execute(text('SELECT root_path,binding_revision FROM workspaces ORDER BY id')).all() == [('/original', 1), (None, 1)]
        assert compare_metadata(MigrationContext.configure(connection, opts={'compare_server_default': True}), Base.metadata) == []
    with upgraded.begin() as connection:
        assert connection.scalar(text("INSERT INTO workspaces(external_id,user_id,name) VALUES ('new',1,'new') RETURNING binding_revision")) == 1
    downgrade(upgraded)
    with upgraded.connect() as connection:
        assert 'binding_revision' not in {c['name'] for c in inspect(connection).get_columns('workspaces')}
        assert connection.scalar(text("SELECT root_path FROM workspaces WHERE external_id='bound'")) == '/original'
    migrate(upgraded)


@pytest.mark.parametrize('value', [None, 0, -1])
def test_database_rejects_invalid_revision(upgraded, value):
    with pytest.raises(IntegrityError), upgraded.begin() as connection:
        connection.execute(text('UPDATE workspaces SET binding_revision=:value WHERE id=1'), {'value': value})
    with upgraded.connect() as connection:
        assert connection.scalar(text('SELECT binding_revision FROM workspaces WHERE id=1')) == 1


def test_downgrade_refuses_to_reset_observed_history(upgraded):
    with upgraded.begin() as connection:
        connection.execute(text('UPDATE workspaces SET binding_revision=2 WHERE id=1'))
    with pytest.raises(RuntimeError, match='不能无损回退'):
        downgrade(upgraded)
    with upgraded.connect() as connection:
        assert connection.scalar(text('SELECT binding_revision FROM workspaces WHERE id=1')) == 2
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(
            Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini')),
        ).get_current_head()
