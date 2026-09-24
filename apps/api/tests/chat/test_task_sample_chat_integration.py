"""真实隔离 PostgreSQL、Task 借用与聊天 NDJSON；模型和 Docker 受控。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import AgentRun, AgentRunEvent, Workspace, Task, Conversation
from app.repositories.chat import conversation_repository
from app.repositories.runtime import run_repository
from app.routers.chat import chat as route
from app.routers.chat.chat_execution import ChatExecution
from app.schemas import ChatRequest
from app.services.workspace.git.task_git_samples import TaskGitSamples
from app.services.chat import chat_service
from app.services.runtime.agent import tool_execution_context
from app.services.runtime.execution.command_recovery_store import CommandRecoveryStore
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.runtime.execution.task_sample_recovery_store import TaskSampleRecoveryStore
from app.services.runtime.sandbox import task_sample_command as commands
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414
from tests.runtime.sandbox.test_sandbox_sample import sample_base as sample_base  # noqa: PLC0414
from tests.runtime.sandbox import test_sandbox_sample_command as command_fixtures

docker_lab = command_fixtures.lab
from tests.runtime.sandbox.test_sandbox_creation import request
from tests.model.test_model_decision import build_text_response, build_tool_response


@pytest.mark.parametrize('outcome', ['success', 'nonzero', 'cleanup', 'busy_after_ready', 'moved_after_recheck'])
def test_route_stream_task_command_persistence_and_close(setup, docker_lab, engine, target, monkeypatch, outcome):
    bindings, scope, _, _ = setup
    bindings.bind(**scope)
    sessions = sessionmaker(bind=engine)
    for module in (conversation_repository, run_repository, tool_execution_context):
        monkeypatch.setattr(module, 'SessionLocal', sessions)
    monkeypatch.setattr(settings, 'agent_max_total_tokens', None)
    async def monitor(*args):
        await asyncio.Future()
    monkeypatch.setattr(chat_service, 'wait_for_run_cancellation', monitor)
    original = commands.create_task_sandbox_snapshot
    def capture(**kwargs):
        assert kwargs['expected_context'].task_id == target['task_id']
        if outcome == 'moved_after_recheck':
            with Session(engine) as session, session.begin():
                alternative = Task(external_id='f' * 32, title='moved', workspace=session.scalar(select(Workspace)))
                session.add(alternative)
                session.flush()
                session.get(Conversation, target['conversation_pk']).task_id = alternative.id
            try:
                return original(**kwargs)
            finally:
                with Session(engine) as session, session.begin():
                    session.get(Conversation, target['conversation_pk']).task_id = target['task_pk']
        if outcome == 'busy_after_ready':
            with bindings.borrow(**scope), pytest.raises(TaskSampleBindingError) as caught:
                original(**kwargs)
            # 另一个借用者正常归还；当前命令保留自己借用失败的事实。
            raise caught.value
        sample = original(**kwargs)
        docker_lab.samples.append(sample)
        return sample
    monkeypatch.setattr(commands, 'create_task_sandbox_snapshot', capture)
    if outcome == 'nonzero':
        docker_lab.code = 7
    elif outcome == 'cleanup':
        docker_lab.failure = 'remove'
    create = AsyncMock(side_effect=[
        build_tool_response(('command-call', 'run_command', json.dumps({'argv': request().argv}))),
        build_text_response('done'),
    ])
    monkeypatch.setattr(chat_service, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    git_manager = TaskGitSamples()
    owner = ChatExecution(
        user_id=target['user_id'], body=ChatRequest(session_id=target['conversation_id'], prompt='test'),
        threads=ExecutionThreads(), command_recovery_store=CommandRecoveryStore(),
        task_sample_recovery_store=TaskSampleRecoveryStore(), sample_bindings=bindings,
        git_samples_provider=lambda: git_manager,
    )
    async def scenario():
        response = await route.chat_stream(owner)
        try:
            events = [json.loads(line) async for line in response.body_iterator]
            assert events[-1]['type'] == 'RUN_FINISHED'
            expected = 'TOOL_CALL_RESULT' if outcome in ('success', 'nonzero') else 'TOOL_CALL_ERROR'
            assert any(item['type'] == expected for item in events)
        finally:
            await owner.close()
            git_manager.shutdown()
        return owner.creation.result()
    run_id = asyncio.run(scenario())
    stored = owner.task_sample_recovery_store.get(
        user_id=target['user_id'], conversation_id=target['conversation_id'], run_id=run_id,
    )
    assert stored is owner.sample_scope and owner.command_scope is None
    record = stored.journal.records[0]
    assert record.status == ('completed' if outcome in ('success', 'nonzero') else 'unconfirmed')
    assert bindings.read_status(**scope).status == 'ready'
    if outcome in ('busy_after_ready', 'moved_after_recheck'):
        assert docker_lab.calls == [] and not record.recovery.create_attempted
    elif outcome == 'cleanup':
        assert record.recovery.sample is docker_lab.samples[0] and record.recovery.sample.root.exists()
    else:
        assert json.loads(record.command_json)['exit_code'] == (7 if outcome == 'nonzero' else 0)
    tool = next(item['function'] for item in create.call_args.kwargs['tools'] if item['function']['name'] == 'run_command')
    assert '/workspace/example.txt' in tool['description']
    assert set(tool['parameters']['properties']) == {'argv', 'working_directory'}
    with Session(engine) as session:
        assert session.get(AgentRun, run_id).status == 'done'
        assert session.scalar(select(Workspace.root_path)) is not None
        types = session.scalars(select(AgentRunEvent.event_type).where(AgentRunEvent.run_id == run_id)).all()
        assert ('TOOL_CALL_RESULT' if outcome in ('success', 'nonzero') else 'TOOL_CALL_ERROR') in types
    bindings.close(**scope)
