"""跨文件预览与事务保存，不允许失败时留下部分提案。"""
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session, sessionmaker
from app.models import FileEditProposal
from app.services.workspace.proposals import patch_batch as service
from tests.workspace.proposals.test_project_snapshot import context
from tests.workspace.proposals.test_project_write_execution import grants, ready, setup, saved, root, target, database, execution_database
__all__ = ['context', 'database', 'execution_database', 'grants', 'ready', 'root', 'saved', 'setup', 'target']

@pytest.mark.parametrize('failed', [False, True])
def test_batch_commits_all_or_none(context, root, engine, setup, monkeypatch, failed):
    monkeypatch.setattr(service, 'SessionLocal', sessionmaker(bind=engine, class_=setup[3]))
    (root / 'a.py').write_text('old\n')
    (root / 'b.py').write_text('old\n')
    patches = [{'relative_path': name, 'patch': f'--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-old\n+new\n'}
               for name in ['a.py', 'b.py']]
    if failed:
        patches[1]['patch'] = patches[1]['patch'].replace('-old', '-wrong')
        with pytest.raises(ValueError): service.create_patch_batch(context=context, patches=patches)
    else:
        result = service.create_patch_batch(context=context, patches=patches)
        assert len(result) == 2 and all(x['status'] == 'pending' for x in result)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(FileEditProposal)) == (1 if failed else 3)
    assert (root / 'a.py').read_text() == (root / 'b.py').read_text() == 'old\n'
