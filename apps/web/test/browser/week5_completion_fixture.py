"""第五周最终PC验收：真实本地身份/PG/文件，模型决策为固定测试替身。"""
import json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Workspace, Task, Conversation, TaskChangeSet, OwnedWorkArea, AgentRunEvent
from app.services.auth.local_identity import resolve_local_identity
OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/week5-completion'
OPERATIONS = [
    {'kind': 'create', 'path': 'new.txt', 'content': 'new\n'},
    {'kind': 'update', 'path': 'edit.txt', 'patch': '--- a/edit.txt\n+++ b/edit.txt\n@@ -1 +1 @@\n-old\n+updated\n'},
    {'kind': 'delete', 'path': 'delete.txt'},
    {'kind': 'move', 'path': 'move.txt', 'destination': 'moved.txt'},
]

def seed(engine, directory):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    root = directory.resolve() / 'week5-project'
    root.mkdir()
    for name, content in [('edit.txt', 'old\n'), ('delete.txt', 'delete\n'), ('move.txt', 'move\n')]:
        (root / name).write_text(content)
    with Session(engine) as session:
        owner = resolve_local_identity(session).id
    with Session(engine) as session, session.begin():
        workspace = Workspace(external_id=uuid4().hex, user_id=owner, name='第五周验收', root_path=str(root), binding_revision=1)
        session.add(workspace); session.flush()
        task = Task(external_id=uuid4().hex, workspace_id=workspace.id, title='完整产品闭环')
        session.add(task); session.flush()
        session.add(Conversation(external_id=uuid4().hex, user_id=owner, task_id=task.id))
        info = {'workspace_id': workspace.external_id, 'task_id': task.external_id, 'root': str(root),
                'managed_base': str(directory.resolve() / 'managed-workspaces')}
    (OUTPUT / 'fixture.json').write_text(json.dumps(info))

def decision(observations):
    from app.services.runtime.agent.agent_runtime import ToolAction, FinalAnswer, ToolErrorObservation, ModelUsage
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if not observations:
        return ToolAction(tool_call_id='week5-general', tool_name='create_change_set',
                          arguments=json.dumps({'operations': OPERATIONS}), model_usage=usage)
    if isinstance(observations[-1], ToolErrorObservation):
        return FinalAnswer(content='变更组调用失败', model_usage=usage)
    assert json.loads(observations[-1].result)['status'] == 'pending'
    return FinalAnswer(content='通用变更组已保存，等待用户审阅。', model_usage=usage)

def verify(engine):
    fixture = json.loads((OUTPUT / 'fixture.json').read_text())
    root = Path(fixture['root'])
    assert (root / 'edit.txt').read_text() == 'old\n'
    assert (root / 'delete.txt').read_text() == 'delete\n'
    assert (root / 'move.txt').read_text() == 'move\n'
    assert not (root / 'new.txt').exists() and not (root / 'moved.txt').exists()
    with Session(engine) as session:
        groups = list(session.scalars(select(TaskChangeSet)))
        areas = list(session.scalars(select(OwnedWorkArea)))
        assert len(groups) == 2 and len(areas) == 1
        assert sorted(row.status for row in groups) == ['applied', 'rolled_back']
        assert areas[0].exported_id == next(row.external_id for row in groups if row.status == 'rolled_back')
        copied = Path(areas[0].bound_root)
        assert (copied / 'edit.txt').read_text() == 'updated\n' and (copied / 'moved.txt').exists()
        events = list(session.scalars(select(AgentRunEvent)))
        assert any(row.event_type == 'TOOL_CALL_RESULT' for row in events)
    (OUTPUT / 'database.json').write_text(json.dumps({'source_restored': True, 'copy_retained': True,
        'exported_pending_then_approved': True, 'statuses': ['applied', 'rolled_back'], 'real_model_called': False}))
