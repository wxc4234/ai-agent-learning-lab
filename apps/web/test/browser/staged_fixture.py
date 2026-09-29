"""隔离暂存观察夹具；合成Git格式对象，经真实HTTP链读取。"""
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Conversation, Task, Workspace
from app.services.auth.local_identity import resolve_local_identity
from tests.workspace.git.test_project_tree import install
from tests.workspace.git.test_loose_tree import entry
from tests.workspace.git.test_index_v2 import fixture as index_bytes, entry as index_entry

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/staged'


def seed(engine, directory):
    assert engine.url.database.startswith('agent_lab_test_')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        owner = resolve_local_identity(session).id
    fixtures = []
    with Session(engine) as session, session.begin():
        for kind in ('modified', 'empty', 'missing', 'unsupported'):
            root = directory.resolve() / f'staged-{kind}'
            git = root / '.git'
            (git / 'objects').mkdir(parents=True)
            (git / 'refs/heads').mkdir(parents=True)
            (git / 'HEAD').write_text('ref: refs/heads/main\n')
            (git / 'config').write_text('[core]\nrepositoryformatversion = 0\nbare = false\n')
            install(git, entry(b'file.txt', oid=b'\x12' * 20))
            if kind != 'missing':
                (git / 'index').write_bytes(index_bytes(index_entry(oid=b'\x34' * 20 if kind == 'modified' else b'\x12' * 20)))
            if kind == 'unsupported':
                (git / 'index').write_bytes(b'bad')
            workspace = Workspace(external_id=uuid4().hex, user_id=owner, name=f'暂存-{kind}', root_path=str(root), binding_revision=1)
            session.add(workspace)
            session.flush()
            task = Task(external_id=uuid4().hex, workspace_id=workspace.id, title=f'暂存-{kind}')
            session.add(task)
            session.flush()
            session.add(Conversation(external_id=uuid4().hex, user_id=owner, task_id=task.id, title='暂存验收'))
            fixtures.append({'kind': kind, 'workspace_id': workspace.external_id, 'task_id': task.external_id})
    (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))
