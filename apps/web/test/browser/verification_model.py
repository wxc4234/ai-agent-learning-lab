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
from app.services.runtime.verification.contracts import VerificationRequest
from app.tools.registry import ToolDefinition

OUTPUT = Path(__file__).resolve().parents[2] / 'output/playwright/verification'
MODES = ('passed', 'failed', 'unavailable', 'zero', 'truncated', 'invalid')


def verification_decision(observations):
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    if observations:
        return FinalAnswer(content='样例验证结果已记录。', model_usage=usage)
    return ToolAction(tool_call_id='verification-browser', tool_name='verify_task_sample',
        arguments='{"plan_id":"sample_unittest_v1"}', model_usage=usage)


def payload(mode):
    command = {'status': 'exited', 'exit_code': 1 if mode == 'failed' else 0, 'oom_killed': False,
        'daemon_error': False, 'stdout_truncated': False, 'stderr_truncated': mode == 'truncated', 'duration_ms': 12}
    counts = {'tests_run': 0 if mode == 'zero' else 1, 'successful_tests': 1 if mode in ('passed', 'truncated') else 0,
        'failures': 1 if mode == 'failed' else 0, 'errors': 0, 'skipped': 0, 'expected_failures': 0, 'unexpected_successes': 0}
    value = {'source': 'trusted_sample_snapshot', 'scope': 'controlled_sample_only', 'plan_id': 'sample_unittest_v1',
        'outcome': mode if mode in ('passed', 'failed') else 'unconfirmed', 'command': command,
        'report_status': 'unavailable' if mode == 'unavailable' else 'complete', 'counts': None if mode == 'unavailable' else counts}
    return '<img src=x onerror="window.verificationInjected=true">' if mode == 'invalid' else json.dumps(value)


def install_verification_fixture(app):
    original = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for name in ('evidence.json', 'server-evidence.json'):
            (OUTPUT / name).unlink(missing_ok=True)
        calls, fixtures, modes = [], [], {}
        with TemporaryDirectory(prefix='verification-browser-') as directory:
            sentinel = Path(directory) / 'unchanged.txt'
            sentinel.write_bytes(b'not a test target')
            before = sentinel.read_bytes(), sentinel.stat().st_ino, sentinel.stat().st_mtime_ns
            async with original(application):
                with SessionLocal() as session:
                    user = resolve_local_identity(session)
                for mode in MODES:
                    workspace_id, task_id, conversation_id = (uuid4().hex for _ in range(3))
                    with SessionLocal() as session, session.begin():
                        workspace = Workspace(external_id=workspace_id, name=f'验证展示-{mode}', user_id=user.id, root_path=directory)
                        task = Task(external_id=task_id, title=f'验证展示-{mode}', workspace=workspace)
                        session.add_all([workspace, task, Conversation(external_id=conversation_id, user_id=user.id, task=task)])
                    fixtures.append({'mode': mode, 'workspace_id': workspace_id, 'task_id': task_id})
                    modes[task_id] = mode
                (OUTPUT / 'fixtures.json').write_text(json.dumps(fixtures))
                async def bind(owner, context):
                    async def execute(**kwargs):
                        calls.append(context.task_id)
                        return payload(modes[context.task_id])
                    return ToolDefinition(name='verify_task_sample', description='受控展示协议夹具',
                        arguments_model=VerificationRequest, async_executor=execute, requires_context=True)
                with patch.object(ChatExecution, 'bind_verification_tool', bind):
                    yield
                assert len(calls) == 6 and len(set(calls)) == 6
                assert (sentinel.read_bytes(), sentinel.stat().st_ino, sentinel.stat().st_mtime_ns) == before
                with SessionLocal() as session:
                    runs = session.scalars(select(AgentRun)).all()
                    assert len(runs) == 6 and all(run.status == 'done' for run in runs)
        assert not Path(directory).exists()
        (OUTPUT / 'server-evidence.json').write_text(json.dumps({'calls': 6, 'runs': 6, 'source_unchanged': True, 'directory_removed': True}))
    app.router.lifespan_context = lifespan
