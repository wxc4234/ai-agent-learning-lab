"""完整工作台补丁应用夹具：可信登记自建样例，不提供公开测试接口。"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Conversation, FileEditProposal, Task, Workspace, WorkspaceSampleOrigin
from app.services.auth.local_identity import resolve_local_identity
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction
from app.services.workspace.samples.sample_execution_runtime import get_sample_bindings
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError

OUTPUT = Path('/private/tmp/agent-ui-patch-application/output/playwright')


def install_patch_application_fixture(app):
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            OUTPUT.mkdir(parents=True, exist_ok=True)
            with SessionLocal() as session:
                user = resolve_local_identity(session)
            bindings = get_sample_bindings()
            fixtures = []
            try:
                for marker in ('success', 'pending', 'truncated', 'conflict'):
                    workspace_id, task_id = uuid4().hex, uuid4().hex
                    with SessionLocal() as session, session.begin():
                        workspace = Workspace(external_id=workspace_id, name=f'补丁应用-{marker}', user_id=user.id)
                        task = Task(external_id=task_id, title=f'补丁应用-{marker}', workspace=workspace)
                        session.add_all([workspace, task, Conversation(external_id=uuid4().hex, user_id=user.id, task=task)])
                    scope = {'user_id': user.id, 'workspace_id': workspace_id, 'task_id': task_id}
                    bindings.bind(**scope)
                    with bindings.borrow(**scope) as sample:
                        fixtures.append(dict(marker=marker, root=str(sample.root), **scope))
                # 私有控制文件仅供测试进程，不通过网页传递路径或身份。
                (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))
                yield
            finally:
                for item in fixtures:
                    scope = {key: item[key] for key in ('user_id', 'workspace_id', 'task_id')}
                    try:
                        bindings.close(**scope)
                    except TaskSampleBindingError:
                        # HTTP已停止；仅清理本轮自建句柄，不能当成产品恢复流程。
                        binding = bindings._bindings[(item['user_id'], item['workspace_id'], item['task_id'])]
                        assert not binding.busy and binding.state == 'uncertain'
                        with SessionLocal() as session, session.begin():
                            task = session.scalar(select(Task).where(Task.external_id == item['task_id']))
                            rows = list(session.scalars(select(FileEditProposal).where(FileEditProposal.task_id == task.id)))
                            assert all(row.application_status in ('idle', 'applied', 'not_applied') for row in rows)
                            workspace = task.workspace
                            assert workspace.root_path == item['root']
                            workspace.root_path = None
                            origin = session.get(WorkspaceSampleOrigin, workspace.id)
                            session.delete(origin)
                        assert bindings._registry.close(binding.handle)
                    assert not Path(item['root']).exists()
                (OUTPUT / 'cleanup.json').write_text(json.dumps({'samples_removed': len(fixtures)}))
    app.router.lifespan_context = lifespan


def patch_application_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if not observations:
        after = 'X' * 20000 if '[patch-apply-truncated]' in prompt else 'new'
        return ToolAction(tool_call_id='patch-application', tool_name='create_file_patch_proposal',
                          arguments=json.dumps({'relative_path': 'example.txt', 'patch':
                              f'--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+{after}\n'}), model_usage=usage)
    assert json.loads(observations[-1].result)['status'] == 'pending'
    return FinalAnswer(content='补丁提案已保存，请审阅后决定是否批准', model_usage=usage)


def verify_patch_application_rows(engine):
    report = json.loads((OUTPUT / 'evidence.json').read_text())
    fixtures = json.loads((OUTPUT / 'fixtures.json').read_text())
    with Session(engine) as session:
        rows = list(session.scalars(select(FileEditProposal)))
        assert len(rows) == len(report) == 4
        for item, fixture in zip(report, fixtures, strict=True):
            row = next(row for row in rows if row.external_id == item['proposal_id'])
            assert row.status == ('approved' if item['marker'] in ('success', 'conflict') else 'pending')
            assert row.application_status == {'success': 'applied', 'conflict': 'not_applied'}.get(item['marker'], 'idle')
            expected = b'new\n' if item['marker'] == 'success' else b'external\n' if item['marker'] == 'conflict' else b'old\n'
            assert (Path(fixture['root']) / 'example.txt').read_bytes() == expected
    print('PASS database/file: 4 patch proposals, explicit decisions, applied/not_applied/idle verified', flush=True)
