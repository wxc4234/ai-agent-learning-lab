"""仅隔离布局验收：固定提案状态样本，无真实项目写入。"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.database import SessionLocal
from app.models import Conversation, FileEditProposal, Task, Workspace
from app.services.auth.local_identity import resolve_local_identity

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/layout'


def install_layout_fixture(app):
    original = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original(application), _fixture() as fixtures:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            (OUTPUT / 'done').unlink(missing_ok=True)
            (OUTPUT / 'fixture.json').write_text(json.dumps(fixtures))
            yield
    app.router.lifespan_context = lifespan


@asynccontextmanager
async def _fixture():
    with TemporaryDirectory(prefix='layout-only-') as directory:
        with SessionLocal() as session:
            user = resolve_local_identity(session)
        fixtures = []
        for empty in (False, True):
            wid, tid = uuid4().hex, uuid4().hex
            with SessionLocal() as session, session.begin():
                workspace = Workspace(external_id=wid, name='布局验收', user_id=user.id, root_path=directory)
                task = Task(external_id=tid, title='空任务' if empty else '修改审阅', workspace=workspace)
                session.add_all([workspace, task, Conversation(external_id=uuid4().hex, user_id=user.id, task=task)])
                session.flush()
                if not empty:
                    for status, application, name in [
                        ('pending', 'idle', 'src/chat.tsx'), ('approved', 'idle', 'src/styles.css'),
                        ('approved', 'applied', 'README.md'), ('approved', 'uncertain', 'src/runtime.ts'),
                    ]:
                        session.add(FileEditProposal(
                            external_id=uuid4().hex, task_id=task.id, bound_root=directory,
                            relative_path=name, status=status, application_status=application,
                            application_token=uuid4().hex if application != 'idle' else None,
                            baseline_sha256='a'*64, proposed_sha256='b'*64, proposed_content='new',
                            diff='--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new\n', diff_truncated=False,
                        ))
            fixtures.append({'workspace_id': wid, 'task_id': tid, 'empty': empty})
        yield fixtures
