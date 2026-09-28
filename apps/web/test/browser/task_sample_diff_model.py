"""PC展示专项：协议结果与模型受控，真实BFF/流/历史持久化；不执行Docker。"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AgentRun, Conversation, Task, Workspace
from app.routers.chat.chat_execution import ChatExecution
from app.services.auth.local_identity import resolve_local_identity
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelUsage, ToolAction
from hashlib import sha256
from app.tools.task_sample_diff import TaskSampleDiffArguments
from app.tools.registry import ToolDefinition

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/task_sample_diff'
MODES = ('diff', 'empty', 'invalid')


def task_sample_diff_decision(observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if observations:
        return FinalAnswer(content='应用样例差异已记录。', model_usage=usage)
    return ToolAction(tool_call_id='task_sample_diff-browser', tool_name='read_task_sample_diff',
        arguments='{}', model_usage=usage)


def payload(mode):
    baseline = sha256(b'old\n').hexdigest()
    diff = '' if mode == 'empty' else '+<img src=x onerror="window.sampleDiffInjected=true">你好' + 'x' * 240 + '\n'
    value = {'source': 'task_application_sample', 'status': 'complete',
        'comparison': 'fixed_old_to_snapshot', 'path': 'example.txt',
        'baseline_sha256': baseline, 'content_sha256': baseline if mode == 'empty' else sha256(b'new\n').hexdigest(),
        'format': 'git_diff', 'encoding': 'utf-8', 'byte_count': len(diff.encode()), 'diff': diff}
    return '{"source":"task_git_sample"}' if mode == 'invalid' else json.dumps(value)


def install_task_sample_diff_fixture(app):
    original = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name in ('evidence.json', 'server-evidence.json'):
            (OUTPUT / name).unlink(missing_ok=True)
        calls, fixtures, modes = [], [], {}
        with TemporaryDirectory(prefix='task_sample_diff-browser-') as directory:
            sentinel = Path(directory) / 'unchanged.txt'
            sentinel.write_bytes(b'not a test target')
            before = sentinel.read_bytes(), sentinel.stat().st_ino, sentinel.stat().st_mtime_ns
            async with original(application):
                with SessionLocal() as session:
                    user = resolve_local_identity(session)
                for mode in MODES:
                    workspace_id, task_id, conversation_id = (uuid4().hex for _ in range(3))
                    with SessionLocal() as session, session.begin():
                        workspace = Workspace(external_id=workspace_id, name=f'应用差异展示-{mode}', user_id=user.id, root_path=directory)
                        task = Task(external_id=task_id, title=f'应用差异展示-{mode}', workspace=workspace)
                        session.add_all([workspace, task, Conversation(external_id=conversation_id, user_id=user.id, task=task)])
                    fixtures.append({'mode': mode, 'workspace_id': workspace_id, 'task_id': task_id})
                    modes[task_id] = mode
                (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))
                async def bind(owner, context):
                    def execute(**kwargs):
                        calls.append(context.task_id)
                        return payload(modes[context.task_id])
                    return ToolDefinition(name='read_task_sample_diff', description='受控展示协议夹具',
                        arguments_model=TaskSampleDiffArguments, executor=execute, requires_context=True)
                with patch.object(ChatExecution, 'bind_sample_diff_tool', bind):
                    yield
                assert len(calls) == 3 and len(set(calls)) == 3
                assert (sentinel.read_bytes(), sentinel.stat().st_ino, sentinel.stat().st_mtime_ns) == before
                with SessionLocal() as session:
                    runs = session.scalars(select(AgentRun)).all()
                    assert len(runs) == 3 and all(run.status == 'done' for run in runs)
        assert not Path(directory).exists()
        (OUTPUT / 'server-evidence.json').write_text(json.dumps({'calls': 3, 'runs': 3, 'source_unchanged': True, 'directory_removed': True}))
    app.router.lifespan_context = lifespan
