"""真实迁移、数据库约束及许可历史的防丢失回退。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import FileEditProposal, ProjectWriteGrantRecord
from tests.migrations.test_database_readiness import migrate
from tests.workspace.proposals.test_project_write_grants import grants, ready, setup, saved, root, target, database

__all__ = ['database', 'grants', 'ready', 'root', 'saved', 'setup', 'target']


@pytest.fixture
def engine(empty_engine):
    migrate(empty_engine)
    yield empty_engine


def downgrade(engine):
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, 'a4f5d3e8b21c')


def test_upgrade_empty_no_implicit_grants_and_metadata_matches(engine, ready):
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM project_write_grants')) == 0
        assert compare_metadata(MigrationContext.configure(connection, opts={'compare_server_default': True}), Base.metadata) == []
    # 新审计迁移阻止丢弃已创建提案的历史；无许可不等于可无损回退。
    with pytest.raises(RuntimeError, match='audit history'):
        downgrade(engine)
    migrate(engine)
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM project_write_grants')) == 0
        assert connection.scalar(text('SELECT status FROM file_edit_proposals')) == 'approved'


@pytest.mark.parametrize('revoked', [False, True])
def test_downgrade_preserves_existing_history(engine, grants, ready, revoked):
    grant = grants.issue(**ready[0])
    if revoked:
        grant = grants.revoke(**ready[0], grant_id=grant.grant_id, revision=1)
    with pytest.raises(RuntimeError, match='不能无损回退|audit history'):
        downgrade(engine)
    assert grants.read(**ready[0]) == grant


@pytest.mark.parametrize('assignment', [
    'revision=0', 'revision=2', 'enabled=false', "grant_id='bad'", "target='[]'::jsonb", 'target=NULL',
])
def test_database_constraints(engine, grants, ready, assignment):
    grant = grants.issue(**ready[0])
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text('UPDATE project_write_grants SET ' + assignment))
    assert grants.read(**ready[0]) == grant


def test_duplicate_proposal_and_proposal_deletion(engine, grants, ready):
    grant = grants.issue(**ready[0])
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(ProjectWriteGrantRecord(
            grant_id='f' * 32, proposal_id=grant.target.proposal_id,
            target=grant.target.model_dump(mode='json'), revision=1, enabled=True,
        ))
    with Session(engine) as session, session.begin():
        proposal = session.get(FileEditProposal, grant.target.proposal_id)
        assert proposal is not None
        session.delete(proposal)
    with Session(engine) as session:
        assert session.scalar(select(ProjectWriteGrantRecord)) is None
