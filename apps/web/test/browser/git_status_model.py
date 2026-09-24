"""隔离PC验收：仅模型受控，应用manager、Git采集与数据库均使用真实实现。"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AgentRun, AgentRunEvent, Conversation, Task, Workspace
from app.services.auth.local_identity import resolve_local_identity
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation
from app.services.workspace.git.application_samples import get_git_samples
from fastapi import Request

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/git-status'


def git_status_decision(observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if observations:
        observation = observations[-1]
        if isinstance(observation, ToolErrorObservation):
            return FinalAnswer(content=f'临时Git样例查询失败：{observation.details}，未取得状态。', model_usage=usage)
        result = json.loads(observation.result)
        assert result['source'] == 'task_git_sample'
        return FinalAnswer(content=f"临时Git样例共有{len(result['entries'])}个状态条目；这不是用户项目的Git状态。", model_usage=usage)
    return ToolAction(tool_call_id='git-browser', tool_name='git_sample_status', arguments='{}', model_usage=usage)


def snapshot(path):
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns


def install_git_status_fixture(app):
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        roots, files, workspaces, fixtures = [], [], [], []
        with TemporaryDirectory(prefix='git-browser-project-') as directory:
            project = Path(directory).resolve()
            sentinel = project / 'project.txt'
            sentinel.write_text('user project unchanged')
            files.append((sentinel, snapshot(sentinel)))
            async with original(application):
                manager = get_git_samples(Request({'type': 'http', 'app': application}))
                with SessionLocal() as session:
                    user = resolve_local_identity(session)
                for mode in ('changed', 'empty', 'missing', 'invalid-config'):
                    workspace_id, task_id, conversation_id = (uuid4().hex for _ in range(3))
                    # 先提交归属，再创建Git目录；数据库事务不覆盖文件I/O。
                    with SessionLocal() as session, session.begin():
                        workspace = Workspace(external_id=workspace_id, name=f'Git样例-{mode}', user_id=user.id, root_path=str(project))
                        task = Task(external_id=task_id, title=f'Git样例-{mode}', workspace=workspace)
                        session.add_all([workspace, task, Conversation(external_id=conversation_id, user_id=user.id, task=task)])
                    workspaces.append(workspace_id)
                    if mode != 'missing':
                        scope = {'user_id': user.id, 'workspace_id': workspace_id, 'task_id': task_id}
                        manager.bind(**scope)
                        root = manager._bindings[tuple(scope.values())].sample.root
                        roots.append(root)
                        if mode == 'changed':
                            path = root / '中文 sample.txt'
                            path.write_text('sample unchanged')
                            files.append((path, snapshot(path)))
                        if mode == 'invalid-config':
                            # 真实配置身份边界拒绝，不替换Git工具或公开伪成功结果。
                            config = root / '.git/config'
                            config.write_bytes(config.read_bytes() + b'\n# changed by fixture\n')
                            files.append((config, snapshot(config)))
                    fixtures.append({'mode': mode, 'workspace_id': workspace_id, 'task_id': task_id})
                (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))
                try:
                    yield
                finally:
                    assert all(snapshot(path) == before for path, before in files)
                    assert len(manager._bindings) == 3  # missing查询不能自动创建样例。
                    with SessionLocal() as session:
                        rows = session.scalars(select(Workspace).where(Workspace.external_id.in_(workspaces))).all()
                        assert len(rows) == 4 and all(row.root_path == str(project) for row in rows)
                    (OUTPUT / 'server-evidence.json').write_text(json.dumps({'files_unchanged': True, 'project_binding_unchanged': True, 'sample_count': 3}))
            assert all(not root.exists() for root in roots)
            (OUTPUT / 'cleanup-evidence.json').write_text(json.dumps({'owned_git_directories_removed': 3}))
    app.router.lifespan_context = lifespan


def verify_git_status_rows(engine):
    report = json.loads((OUTPUT / 'evidence.json').read_text())
    assert len(report) == 4
    with Session(engine) as session:
        assert len(session.scalars(select(AgentRun)).all()) == 4
        for item in report:
            run = session.get(AgentRun, item['run_id'])
            assert run.status == 'done'
            events = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id)).all()
            assert sum(event.event_type == 'TOOL_CALL_START' for event in events) == 1
            assert sum(event.event_type == 'TOOL_CALL_RESULT' for event in events) == item['result_count']
            assert sum(event.event_type == 'TOOL_CALL_ERROR' for event in events) == item['error_count']
    (OUTPUT / 'database-evidence.json').write_text(json.dumps({'runs': 4, 'one_call_each': True}))
    print('PASS PostgreSQL: four runs, one Git call each, exact result/error counts.', flush=True)
