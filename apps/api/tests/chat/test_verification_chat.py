"""真实Task授权/快照/持久化与受控模型往返；Docker输出边界使用替身。"""

import asyncio
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import AgentRun, AgentRunEvent, Message
from app.repositories.chat import conversation_repository
from app.repositories.runtime import run_repository
from app.routers.chat import chat as route
from app.routers.chat.chat_execution import ChatExecution
from app.schemas import ChatRequest
from app.services.chat import chat_service
from app.services.runtime.execution.execution_threads import ExecutionThreads
from app.services.runtime.execution.verification_recovery_store import VerificationRecoveryStore
from app.services.runtime.sandbox.sandbox_sample import cleanup_sandbox_sample
from app.services.runtime.sandbox.sandbox_sample_command import _prepare_in_thread, SampleCommandCancelled
from tests.runtime.verification.test_recorded_task_verification import recovery
from app.services.runtime.verification import task_verification
from tests.assertions import require_value
from tests.chat.test_chat_tool_context import isolated_history_and_monitor as isolated_history_and_monitor  # noqa: PLC0414
from tests.model.response_stream import build_text_response, build_tool_response
from tests.runtime.command.test_task_command_source import lab as lab  # noqa: PLC0414
from tests.runtime.verification.test_contracts import report
from tests.runtime.verification.test_sandbox_verification import receipt
from tests.runtime.verification.test_task_verification_input import context, root
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414


@pytest.mark.parametrize('mode', ['passed', 'failed', 'invalid', 'missing', 'busy', 'sealed', 'query_error', 'cancel'])
def test_route_capability_roundtrip_and_retained_receipt(lab, target, engine, monkeypatch, mode):
    bindings, scope, _ = lab
    monkeypatch.setattr(settings, 'agent_max_total_tokens', None)
    factory = sessionmaker(bind=engine)
    for module in (conversation_repository, run_repository):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    if mode != 'missing':
        bindings.bind(**scope)
    source_path = None if mode == 'missing' else root(engine) / 'example.txt'
    if source_path is not None:
        source_path.write_bytes(b'old\n' if mode == 'failed' else b'new\n')
        before = source_path.read_bytes(), source_path.stat().st_ino, source_path.stat().st_mtime_ns
    if mode == 'sealed':
        with pytest.raises(RuntimeError), bindings.borrow(**scope):
            raise RuntimeError('seal fixture')
    if mode == 'query_error':
        def fail(**kwargs):
            raise OSError('PRIVATE query')
        monkeypatch.setattr(bindings, 'read_status', fail)
    creates = []
    entered = asyncio.Event()
    async def docker_boundary(**kwargs):
        if mode == 'cancel':
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                raise SampleCommandCancelled(recovery=recovery()) from None
        assert kwargs['prepare_in_thread'] is True
        sample, error, cancelled = await _prepare_in_thread(kwargs['prepare_sample'])
        assert error is None and not cancelled
        snapshot = require_value(sample)
        creates.append(snapshot.token)
        try:
            assert bindings.read_status(**scope).status == 'ready'
            counts = report(tests_run=1, failures=1 if mode == 'failed' else 0)
            frame = json.dumps({'version': 1, 'complete': True, 'report': counts.model_dump()}) + '\n'
            return receipt(stdout=frame, stderr='PRIVATE host trace', exit_code=1 if mode == 'failed' else 0)
        finally:
            cleanup_sandbox_sample(snapshot)
    monkeypatch.setattr(task_verification, '_run_owned_sample_command', docker_boundary)
    create = AsyncMock(side_effect=[
        build_tool_response(('verify', 'verify_task_sample', '{"plan_id":"sample_unittest_v1","argv":[]}' if mode == 'invalid' else '{"plan_id":"sample_unittest_v1"}')),
        build_text_response('仅验证当前样例'),
    ])
    monkeypatch.setattr(chat_service, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    owner = ChatExecution(
        user_id=target['user_id'], body=ChatRequest(session_id=target['conversation_id'], prompt='验证样例'),
        threads=ExecutionThreads(), sample_bindings=bindings, verification_recovery_store=VerificationRecoveryStore(),
    )
    # 隔离本课能力，Git与通用命令不是此用例的执行目标。
    monkeypatch.setattr(owner, 'bind_command_tool', AsyncMock(return_value=None))
    monkeypatch.setattr(owner, 'bind_git_status_tool', lambda ctx: None)
    monkeypatch.setattr(owner, 'bind_git_diff_tool', lambda ctx: None)
    async def scenario():
        try:
            response = await route.chat_stream(owner)
            async def collect():
                return [json.loads(line if isinstance(line, str) else bytes(line)) async for line in response.body_iterator]
            consumer = asyncio.create_task(collect())
            if mode == 'cancel':
                await asyncio.wait_for(entered.wait(), 5)
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
                events = []
            else:
                events = await consumer
            return require_value(owner.creation).result(), events
        finally:
            await owner.close()
    with ExitStack() as leases:
        if mode == 'busy':
            leases.enter_context(bindings.borrow(**scope))
        run_id, events = asyncio.run(scenario())
    if mode != 'cancel':
        assert events[-1]['type'] == ('RUN_ERROR' if mode == 'query_error' else 'RUN_FINISHED')
    if mode == 'query_error':
        assert create.call_count == 0
    elif mode == 'cancel':
        assert create.call_count == 1
        with Session(engine) as session:
            assert require_value(session.get(AgentRun, run_id)).status == 'aborted'
            assert not session.scalars(select(Message).where(
                Message.conversation_id == target['conversation_pk'], Message.role == 'assistant',
            )).all()
    else:
        assert create.call_count == 2
        offered = {item['function']['name'] for item in create.call_args.kwargs['tools']}
        assert ('verify_task_sample' in offered) == (mode in ('passed', 'failed', 'invalid'))
        raw = next(m['content'] for m in create.call_args.kwargs['messages'] if m['role'] == 'tool')
        assert 'PRIVATE' not in raw
        public = json.loads(raw)
        event_type = 'TOOL_CALL_RESULT' if mode in ('passed', 'failed') else 'TOOL_CALL_ERROR'
        assert sum(event['type'] == event_type for event in events) == 1
        with Session(engine) as session:
            event = require_value(session.scalar(select(AgentRunEvent).where(
                AgentRunEvent.run_id == run_id, AgentRunEvent.event_type == event_type,
            )))
            assert event.payload['tool_name'] == 'verify_task_sample'
            if mode in ('passed', 'failed'):
                assert event.payload['result'] == raw and public['outcome'] == mode
            replies = session.scalars(select(Message.content).where(
                Message.conversation_id == target['conversation_pk'], Message.role == 'assistant',
            )).all()
            assert replies == ['仅验证当前样例']
    stored = require_value(owner.verification_recovery_store).get(context=context(target), run_id=run_id)
    if mode in ('passed', 'failed'):
        assert len(creates) == 1
        assert require_value(stored).journal.records[0].status == 'completed'
        assert 'PRIVATE' in require_value(require_value(stored).journal.records[0].command_json)
    elif mode == 'cancel':
        assert require_value(stored).journal.records[0].status == 'cancelled'
        assert require_value(stored).journal.records[0].recovery is not None
        assert creates == []
    else:
        assert creates == [] and stored is None
    if source_path is not None:
        assert (source_path.read_bytes(), source_path.stat().st_ino, source_path.stat().st_mtime_ns) == before
