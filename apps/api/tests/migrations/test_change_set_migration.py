"""有恢复现场或隔离副本时，降级不能删除归属和执行证据。"""
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from tests.migrations.test_database_readiness import migrate
from tests.workspace.proposals.test_owned_areas import scope, context, database, setup, root, target, saved, ready
from app.services.workspace.areas.owned_areas import create_owned_area
from app.services.workspace.proposals.change_sets import create_change_set
__all__ = ['context', 'database', 'ready', 'root', 'saved', 'scope', 'setup', 'target']

@pytest.fixture
def engine(empty_engine):
    migrate(empty_engine)
    yield empty_engine

@pytest.mark.parametrize('kind', ['changes', 'area'])
def test_nonempty_history_refuses_downgrade(engine, scope, kind):
    if kind == 'changes':
        create_change_set(**scope, operations=[{'kind': 'create', 'path': 'new.txt', 'content': 'new'}])
    else:
        create_owned_area(**scope)
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with pytest.raises(RuntimeError, match='refusing to discard'), engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, 'c6b7f50ad43e')
