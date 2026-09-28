"""许可联合夹具：只在隔离库和TemporaryDirectory内准备项目，不替换许可服务。"""

from contextlib import asynccontextmanager
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, FileEditProposal, ProjectWriteGrantRecord, Task, User, Workspace
from app.services.auth.local_identity import resolve_local_identity

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/project-write-grant-integration'


def seed(engine, directory: Path):
    assert engine.url.database.startswith('agent_lab_test_')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ('fixtures.json', 'host.json', 'browser.json', 'database.json'):
        (OUTPUT / name).unlink(missing_ok=True)
    with Session(engine) as session:
        owner = resolve_local_identity(session).id
    fixtures = []
    with Session(engine) as session, session.begin():
        foreign = User(external_id=uuid4().hex)
        session.add(foreign)
        session.flush()
        for marker in ('normal', 'unknown', 'foreign'):
            root = directory.resolve() / marker
            root.mkdir()
            file = root / 'example.txt'
            file.write_bytes(b'old\n')
            info = file.stat()
            uid = foreign.id if marker == 'foreign' else owner
            workspace = Workspace(external_id=uuid4().hex, user_id=uid, name=f'许可-{marker}', root_path=str(root), binding_revision=1)
            session.add(workspace)
            session.flush()
            task = Task(external_id=uuid4().hex, workspace_id=workspace.id, title=f'许可-{marker}')
            session.add(task)
            session.flush()
            session.add(Conversation(external_id=uuid4().hex, user_id=uid, task_id=task.id, title='许可验收'))
            proposal = FileEditProposal(external_id=uuid4().hex, task_id=task.id, bound_root=str(root),
                relative_path='example.txt', baseline_sha256=sha256(b'old\n').hexdigest(),
                proposed_sha256=sha256(b'new\n').hexdigest(), proposed_content='new\n',
                diff='-old\n+new', diff_truncated=False, status='pending')
            session.add(proposal)
            fixtures.append({'marker': marker, 'workspace_id': workspace.external_id, 'task_id': task.external_id,
                'proposal_id': proposal.external_id, 'file': str(file), 'inode': info.st_ino, 'mtime': info.st_mtime_ns})
    (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))


def audit_host(app):
    original = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            # 只记录测试实例身份用于独立对账，不创建公开诊断接口或传递凭证。
            (OUTPUT / 'host.json').write_text(json.dumps({'runtime_id': application.state.project_write_grants._reader._runtime_id}))
            yield
    app.router.lifespan_context = lifespan


def verify(engine):
    fixtures = json.loads((OUTPUT / 'fixtures.json').read_text())
    host = json.loads((OUTPUT / 'host.json').read_text())
    browser = json.loads((OUTPUT / 'browser.json').read_text())
    with Session(engine) as session:
        proposals = list(session.scalars(select(FileEditProposal)))
        grants = list(session.scalars(select(ProjectWriteGrantRecord)))
        assert len(proposals) == 3 and len(grants) == 2
        assert {grant.target['runtime_id'] for grant in grants} == {host['runtime_id']}
        for item in fixtures:
            row = next(p for p in proposals if p.external_id == item['proposal_id'])
            assert row.application_status == 'idle' and row.application_token is None
            assert row.status == ('pending' if item['marker'] == 'foreign' else 'approved')
            grant = next((g for g in grants if g.proposal_id == row.id), None)
            if item['marker'] == 'foreign':
                assert grant is None
            else:
                assert grant is not None
                assert grant.enabled == (item['marker'] == 'unknown')
                assert grant.revision == (1 if grant.enabled else 2)
                assert browser[item['marker']]['grant_id'] == grant.grant_id
            file = Path(item['file'])
            assert file.read_bytes() == b'old\n'
            assert (file.stat().st_ino, file.stat().st_mtime_ns) == (item['inode'], item['mtime'])
    (OUTPUT / 'database.json').write_text(json.dumps({
        'proposals': 3, 'grants': 2, 'same_live_host': True,
        'files_unchanged': True, 'all_applications_idle': True, 'foreign_grants': 0,
    }))
    print('PASS PostgreSQL: 2 exact grants, same live host; 3 files unchanged; no application claimed', flush=True)
