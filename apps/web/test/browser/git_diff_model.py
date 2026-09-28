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
from tests.workspace.git.test_status_capture import git, snapshot as tree_snapshot
from app.services.auth.local_identity import resolve_local_identity
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction, ToolErrorObservation
from app.services.workspace.git.application_samples import get_git_samples
from fastapi import Request

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/git-diff'




def git_diff_decision(prompt, observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if observations:
        observation = observations[-1]
        if isinstance(observation, ToolErrorObservation):
            return FinalAnswer(content=f'临时Git样例查询失败：{observation.details}，未取得差异。', model_usage=usage)
        result = json.loads(observation.result)
        assert result['source'] == 'task_git_sample'
        return FinalAnswer(content=f"样例{result['scope']}差异已读取；这不是用户项目。", model_usage=usage)
    return ToolAction(tool_call_id='git-browser', tool_name='git_sample_diff', arguments=json.dumps({'scope': 'staged' if '[staged]' in prompt else 'worktree'}), model_usage=usage)


def snapshot(path):
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns


def install_git_diff_fixture(app):
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        # 每轮证据独立，避免前次成功文件掩盖本轮清理失败。
        for name in ('evidence.json', 'server-evidence.json', 'cleanup-evidence.json', 'database-evidence.json'):
            (OUTPUT / name).unlink(missing_ok=True)
        roots, files, workspaces, fixtures, trees = [], [], [], [], []
        calls = []
        with TemporaryDirectory(prefix='git-browser-project-') as directory:
            project = Path(directory).resolve()
            sentinel = project / 'project.txt'
            sentinel.write_text('user project unchanged')
            files.append((sentinel, snapshot(sentinel)))
            async with original(application):
                manager = get_git_samples(Request({'type': 'http', 'app': application}))
                with SessionLocal() as session:
                    user = resolve_local_identity(session)
                original_read = manager.read_diff
                def tracked_read(**kwargs):
                    calls.append(kwargs)
                    return original_read(**kwargs)
                manager.read_diff = tracked_read
                for mode in ('worktree', 'staged', 'empty', 'missing', 'missing-head'):
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
                        handle = manager._bindings[tuple(scope.values())].sample
                        if mode != 'missing-head':
                            path = root / '中文 sample.txt'
                            path.write_text('value = 1\n')
                            git(handle, 'add', '--all')
                            git(handle, 'commit', '-qm', 'isolated diff baseline')
                            if mode in ('worktree', 'staged'):
                                path.write_text('value = 2\n')
                                git(handle, 'add', '--all')
                                # 浏览器必须把这些字节作为文本，不创建img或执行脚本。
                                path.write_text('value = 3\n<img src=x onerror="window.diffInjected=true">\n' + 'long-line-' * 90 + '\n')
                        trees.append((root, tree_snapshot(root)))
                    fixtures.append({'mode': mode, 'workspace_id': workspace_id, 'task_id': task_id})
                (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))
                try:
                    yield
                finally:
                    assert all(snapshot(path) == before for path, before in files)
                    assert len(manager._bindings) == 4  # missing查询不能自动创建样例。
                    with SessionLocal() as session:
                        rows = session.scalars(select(Workspace).where(Workspace.external_id.in_(workspaces))).all()
                        assert len(rows) == 5 and all(row.root_path == str(project) for row in rows)
                    assert all(tree_snapshot(root) == before for root, before in trees)
                    assert len(calls) == 5
                    (OUTPUT / 'server-evidence.json').write_text(json.dumps({'files_unchanged': True, 'project_binding_unchanged': True, 'sample_count': 4, 'read_calls': len(calls)}))
            assert all(not root.exists() for root in roots)
            (OUTPUT / 'cleanup-evidence.json').write_text(json.dumps({'owned_git_directories_removed': 4}))
    app.router.lifespan_context = lifespan



from tests.assertions import require_value

def verify_git_diff_rows(engine):
    report = json.loads((OUTPUT / 'evidence.json').read_text())
    assert len(report) == 5
    with Session(engine) as session:
        assert len(session.scalars(select(AgentRun)).all()) == 5
        for item in report:
            run = session.get(AgentRun, item['run_id'])
            assert require_value(run).status == 'done'
            events = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == require_value(run).id)).all()
            assert sum(event.event_type == 'TOOL_CALL_START' for event in events) == 1
            assert sum(event.event_type == 'TOOL_CALL_RESULT' for event in events) == item['result_count']
            assert sum(event.event_type == 'TOOL_CALL_ERROR' for event in events) == item['error_count']
    (OUTPUT / 'database-evidence.json').write_text(json.dumps({'runs': 5, 'one_call_each': True}))
    print('PASS PostgreSQL: five runs, one Git call each, exact result/error counts.', flush=True)
