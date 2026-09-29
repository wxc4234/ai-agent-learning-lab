"""持有式副本隔离与导出：原目录只在新的显式审批应用后改变。"""
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from app.models import Workspace
from app.services.workspace.areas import owned_areas as areas
from app.services.workspace.proposals import change_sets as changes
from tests.workspace.proposals.test_project_snapshot import context
from tests.workspace.proposals.test_project_write_execution import database, setup, root, target, saved, ready
__all__ = ['context', 'database', 'ready', 'root', 'saved', 'setup', 'target']

@pytest.fixture
def scope(engine, setup, ready, context, tmp_path, monkeypatch):
    factory = sessionmaker(bind=engine, class_=setup[3])
    monkeypatch.setattr(areas, 'SessionLocal', factory)
    monkeypatch.setattr(changes, 'SessionLocal', factory)
    monkeypatch.setattr(areas, 'AREA_BASE', tmp_path / 'managed')
    return {key: value for key, value in ready[0].items() if key != 'proposal_id'}


def copy(scope, engine):
    result = areas.create_owned_area(**scope)
    with Session(engine) as session:
        workspace = session.scalar(select(Workspace).where(Workspace.external_id == result['workspace_id']))
        assert workspace is not None and workspace.root_path is not None
        path = Path(workspace.root_path)
    return result, path, {'user_id': scope['user_id'], 'workspace_id': result['workspace_id'], 'task_id': result['task_id']}


def test_hold_export_approve_apply(scope, root, engine):
    (root / 'work.py').write_text('old\n')
    result, path, child = copy(scope, engine)
    (path / 'work.py').write_text('new\n')
    (path / 'created.py').write_text('created\n')
    assert (root / 'work.py').read_text() == 'old\n' and not (root / 'created.py').exists()
    assert areas.read_owned_area(**child) == result
    exported = areas.export_owned_area(**child)
    assert areas.export_owned_area(**child) == exported
    change_id = exported['exported_change_id']
    assert changes.list_change_sets(**scope)[0]['status'] == 'pending'
    changes.decide_change_set(**scope, change_id=change_id, action='approve')
    assert changes.execute_change_set(**scope, change_id=change_id)['status'] == 'applied'
    assert (root / 'work.py').read_text() == 'new\n' and (root / 'created.py').read_text() == 'created\n'
    assert areas.list_owned_areas(**scope)['copies'][0]['workspace_id'] == child['workspace_id']


def test_export_conflict_preserves_original(scope, root, engine):
    (root / 'work.py').write_text('old\n')
    _, path, child = copy(scope, engine)
    (path / 'work.py').write_text('candidate\n')
    (root / 'work.py').write_text('external\n')
    with pytest.raises(ValueError, match='conflict'): areas.export_owned_area(**child)
    assert (root / 'work.py').read_text() == 'external\n'
    assert changes.list_change_sets(**scope) == []


def test_owned_copy_rejects_links_and_foreign_users(scope, root, engine):
    from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
    (root / 'secret').write_text('old')
    result, path, child = copy(scope, engine)
    (path / 'link').symlink_to(root / 'secret')
    with pytest.raises(ValueError): areas.export_owned_area(**child)
    with pytest.raises(WorkspaceNotAccessibleError): areas.list_owned_areas(**{**child, 'user_id': 999999})
    assert str(root) not in str(result)
