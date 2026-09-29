"""真实assessment联调：独立数据库对账，不替换许可宿主或策略。"""

from contextlib import asynccontextmanager
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, FileEditProposal, ProjectWriteGrantRecord, Task, User, Workspace
from app.services.auth.local_identity import resolve_local_identity

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/project-write-assessment-integration'


def seed(engine, directory: Path):
    assert engine.url.database.startswith('agent_lab_test_')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ('fixtures.json', 'host.json', 'browser.json', 'database.json', 'assessment-audit.json', 'approval-diagnostic.json', 'failure.json', 'failure.png'):
        (OUTPUT / name).unlink(missing_ok=True)
    with Session(engine) as session:
        owner = resolve_local_identity(session).id
    fixtures = []
    # 数据准备独立提交；assessment本身不得改变这些行或领取应用机会。
    with Session(engine) as session, session.begin():
        foreign = User(external_id=uuid4().hex)
        session.add(foreign)
        session.flush()
        for marker in ('normal', 'changed', 'foreign'):
            root = directory.resolve() / ('assessment-' + marker)
            root.mkdir()
            file = root / 'example.txt'
            file.write_bytes(b'old\n')
            uid = foreign.id if marker == 'foreign' else owner
            workspace = Workspace(external_id=uuid4().hex, user_id=uid, name=f'前置检查-{marker}', root_path=str(root), binding_revision=1)
            session.add(workspace)
            session.flush()
            task = Task(external_id=uuid4().hex, workspace_id=workspace.id, title=f'前置检查-{marker}')
            session.add(task)
            session.flush()
            session.add(Conversation(external_id=uuid4().hex, user_id=uid, task_id=task.id, title='前置检查联合验收'))
            proposal = FileEditProposal(external_id=uuid4().hex, task_id=task.id, bound_root=str(root),
                relative_path='example.txt', baseline_sha256=sha256(b'old\n').hexdigest(),
                proposed_sha256=sha256(b'new\n').hexdigest(), proposed_content='new\n',
                diff='-old\n+new', diff_truncated=False, status='pending')
            session.add(proposal)
            fixtures.append({'marker': marker, 'workspace_id': workspace.external_id, 'task_id': task.external_id,
                'proposal_id': proposal.external_id, 'file': str(file)})
    (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))


def snapshot(engine):
    fixtures = json.loads((OUTPUT / 'fixtures.json').read_text())
    # 每次使用独立连接读取已提交事实；文件观察在数据库事务结束后进行。
    with engine.connect() as connection:
        rows = {model.__tablename__: [dict(row) for row in connection.execute(
            select(model.__table__).order_by(*model.__table__.primary_key)).mappings()]
            for model in (Workspace, FileEditProposal, ProjectWriteGrantRecord)}
    files = {}
    for item in fixtures:
        file = Path(item['file'])
        info = file.stat()
        files[item['marker']] = {'sha256': sha256(file.read_bytes()).hexdigest(),
            'inode': str(info.st_ino), 'mtime_ns': str(info.st_mtime_ns), 'size': info.st_size}
    # 固化JSON防止可变值别名让前后对账误判相等，不记录运行令牌。
    return json.loads(json.dumps({'rows': rows, 'files': files}, default=str))


def audit_host(app):
    from app.database import engine

    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            (OUTPUT / 'host.json').write_text(json.dumps({'runtime_id': application.state.project_write_grants._reader._runtime_id}))
            yield

    app.router.lifespan_context = lifespan
    audits = []

    @app.middleware('http')
    async def assess_audit(request, call_next):
        if not request.url.path.endswith('/write-grant/assessment'):
            return await call_next(request)
        before = snapshot(engine)
        response = await call_next(request)
        after = snapshot(engine)
        audits.append({'path': request.url.path, 'status': response.status_code,
            'unchanged': before == after,
            'before_sha256': sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
            'after_sha256': sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()})
        (OUTPUT / 'assessment-audit.json').write_text(json.dumps(audits, indent=4))
        assert before == after, 'assessment changed persistent or filesystem state'
        return response


def verify(engine):
    fixtures = json.loads((OUTPUT / 'fixtures.json').read_text())
    browser = json.loads((OUTPUT / 'browser.json').read_text())
    host = json.loads((OUTPUT / 'host.json').read_text())
    audits = json.loads((OUTPUT / 'assessment-audit.json').read_text())
    assert len(audits) == browser['backend_assessments']
    assert audits and all(row['unchanged'] and row['before_sha256'] == row['after_sha256'] for row in audits)
    with Session(engine) as session:
        proposals = list(session.scalars(select(FileEditProposal)))
        grants = list(session.scalars(select(ProjectWriteGrantRecord)))
        assert len(proposals) == 3 and len(grants) == 2
        assert {grant.target['runtime_id'] for grant in grants} == {host['runtime_id']}
        for item in fixtures:
            proposal = next(row for row in proposals if row.external_id == item['proposal_id'])
            assert proposal.application_status == 'idle' and proposal.application_token is None
            assert proposal.status == ('pending' if item['marker'] == 'foreign' else 'approved')
            grant = next((row for row in grants if row.proposal_id == proposal.id), None)
            if item['marker'] == 'foreign':
                assert grant is None
            else:
                assert grant is not None
                assert grant.grant_id == browser['grants'][item['marker']]['grant_id']
                assert (grant.enabled, grant.revision) == ((False, 2) if item['marker'] == 'normal' else (True, 1))
    # 外部编辑只由测试脚本显式制造；最终状态必须准确保留，不能被检查覆盖。
    final = snapshot(engine)
    assert final['files'] == browser['final_files']
    assert final['files']['changed']['sha256'] == sha256(b'external edit\n').hexdigest()
    for marker in ('normal', 'foreign'):
        assert final['files'][marker]['sha256'] == sha256(b'old\n').hexdigest()
    (OUTPUT / 'database.json').write_text(json.dumps({'assessments_audited': len(audits),
        'all_assessments_unchanged': True, 'same_live_host': True, 'all_applications_idle': True,
        'exact_grant_revisions': True, 'external_edit_preserved': True}, indent=4))
    print(f'PASS independent PostgreSQL/files: {len(audits)} read-only assessments, no application claim, exact grants and external edit preserved', flush=True)
