"""真实PostgreSQL授权与文件快照；不启动Docker或模型。"""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Task, Workspace
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError
from app.services.runtime.agent.tool_execution_context import load_tool_execution_context
from app.services.runtime.command.task_command_source import TaskCommandSource, TaskCommandSourceUnavailable
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.verification import task_verification_input as service
from app.services.runtime.verification.contracts import VerificationRequest
from app.services.workspace.samples.task_sample_binding import TaskSampleBindingError
from tests.assertions import require_value
from tests.runtime.command.test_task_command_source import lab as lab  # noqa: PLC0414
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414


def context(target):
    return load_tool_execution_context(user_id=target['user_id'], conversation_id=target['conversation_id'])


def root(engine):
    with Session(engine) as session:
        return Path(require_value(session.scalar(select(Workspace.root_path))))


def test_snapshot_after_return_independent_and_context_retained(lab, target, engine, monkeypatch):
    bindings, scope, sessions = lab
    bindings.bind(**scope)
    source_path = root(engine) / 'example.txt'
    source_path.write_bytes(b'new\n')
    before = source_path.stat()
    expected = context(target)
    factory = service.create_sandbox_snapshot
    def create(**kwargs):
        assert bindings.read_status(**scope).status == 'ready'
        assert all(s.closed and not s.in_transaction() for s in sessions)
        return factory(**kwargs)
    monkeypatch.setattr(service, 'create_sandbox_snapshot', create)
    result = asyncio.run(service.prepare_task_verification_input(
        request=VerificationRequest(plan_id='sample_unittest_v1'), context=expected, bindings=bindings,
    ))
    try:
        assert result.context == expected and result.request.plan_id == 'sample_unittest_v1'
        assert result.sample.root != source_path.parent and result.sample.file_mode == 0o444
        assert source_path.read_bytes() == b'new\n'
        assert source_path.stat().st_ino == before.st_ino and source_path.stat().st_mtime_ns == before.st_mtime_ns
        payload = (result.sample.root / 'example.txt').read_bytes()
        assert payload == service.build_task_verification_source(b'new\n')
        source_path.write_bytes(b'changed later')
        bindings.close(**scope)
        assert not source_path.exists()
        assert (result.sample.root / 'example.txt').read_bytes() == payload
        assert samples.confirm_sandbox_sample_source(result.sample)
    finally:
        samples.cleanup_sandbox_sample(result.sample)


@pytest.mark.parametrize('kind', [
    'wrong_user', 'wrong_workspace', 'wrong_task', 'wrong_conversation',
    'sibling', 'missing', 'busy', 'sealed', 'read_failure', 'exit_failure', 'oversize',
])
def test_rejected_input_never_creates_snapshot(lab, target, engine, monkeypatch, kind):
    bindings, scope, _ = lab
    if kind != 'missing':
        bindings.bind(**scope)
    expected = context(target)
    if kind == 'wrong_user':
        expected = replace(expected, user_id=target['other_id'])
    elif kind.startswith('wrong_'):
        expected = replace(expected, **{kind.removeprefix('wrong_') + '_id': 'f' * 32})
    elif kind == 'sibling':
        expected = load_tool_execution_context(user_id=target['user_id'], conversation_id='e' * 32)
    elif kind == 'sealed':
        with pytest.raises(RuntimeError), bindings.borrow(**scope):
            raise RuntimeError('seal fixture')
    elif kind == 'read_failure':
        (root(engine) / 'example.txt').chmod(0o644)
    elif kind == 'oversize':
        (root(engine) / 'example.txt').write_bytes(b'x' * (service.MAX_TASK_VERIFICATION_BYTES + 1))
    elif kind == 'exit_failure':
        borrow = service.borrow_task_command_source
        @contextmanager
        def bad_exit(**kwargs):
            with borrow(**kwargs) as borrowed:
                yield borrowed
                raise OSError('injected borrow return failure')
        monkeypatch.setattr(service, 'borrow_task_command_source', bad_exit)
    def forbidden(**kwargs):
        pytest.fail('must not create target')
    monkeypatch.setattr(service, 'create_sandbox_snapshot', forbidden)
    errors = (TaskSampleBindingError, TaskCommandSourceUnavailable, ConversationNotAccessibleError, OSError, ValueError)
    try:
        if kind == 'busy':
            with bindings.borrow(**scope), pytest.raises(errors):
                service.create_task_verification_snapshot(context=expected, bindings=bindings)
        else:
            with pytest.raises(errors):
                service.create_task_verification_snapshot(context=expected, bindings=bindings)
        assert not any(value.busy for value in bindings._bindings.values())
        if kind == 'oversize':
            assert bindings.read_status(**scope).status == 'ready'
    finally:
        if kind == 'read_failure':
            (root(engine) / 'example.txt').chmod(0o600)


def test_context_changed_during_borrow_rejected(lab, target, engine, monkeypatch):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    expected = context(target)
    borrow = bindings.borrow
    @contextmanager
    def moved(**kwargs):
        with borrow(**kwargs) as sample:
            with Session(engine) as session, session.begin():
                sibling = Task(
                    external_id='f' * 32, title='迁移目标',
                    workspace=require_value(session.scalar(select(Workspace))),
                )
                session.add(sibling)
                session.flush()
                require_value(session.get(Conversation, target['conversation_pk'])).task_id = sibling.id
            yield sample
    monkeypatch.setattr(bindings, 'borrow', moved)
    monkeypatch.setattr(service, 'create_sandbox_snapshot', lambda **kw: pytest.fail('must not create'))
    with pytest.raises(TaskCommandSourceUnavailable):
        service.create_task_verification_snapshot(context=expected, bindings=bindings)
    assert not any(value.busy for value in bindings._bindings.values())


@pytest.mark.parametrize('outcome', ['snapshot', 'partial', 'failed'])
def test_repeated_cancel_joins_real_borrow_and_keeps_resources(lab, target, monkeypatch, outcome):
    bindings, scope, sessions = lab
    bindings.bind(**scope)
    expected = context(target)
    entered, release = Event(), Event()
    read = TaskCommandSource.read_sample_bytes
    def blocked_read(self):
        content = read(self)
        entered.set()
        assert release.wait(5)
        return content
    monkeypatch.setattr(TaskCommandSource, 'read_sample_bytes', blocked_read)
    if outcome == 'partial':
        def fail_write(*args):
            raise OSError('injected target write failure')
        monkeypatch.setattr(samples.os, 'write', fail_write)
    elif outcome == 'failed':
        def fail_create(**kwargs):
            raise ValueError('PRIVATE')
        monkeypatch.setattr(service, 'create_sandbox_snapshot', fail_create)
    async def scenario():
        task = asyncio.create_task(service.prepare_task_verification_input(
            request=VerificationRequest(plan_id='sample_unittest_v1'), context=expected, bindings=bindings,
        ))
        try:
            async def wait_entered():
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(wait_entered(), 3)
            assert all(s.closed and not s.in_transaction() for s in sessions)
            assert bindings.read_status(**scope).status == 'busy'
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(service.TaskVerificationPreparationCancelled) as caught:
            await task
        recovery = caught.value.recovery
        assert task.cancelled() and recovery.context == expected
        assert recovery.preparation_failed is (outcome != 'snapshot')
        assert bindings.read_status(**scope).status == 'ready'
        if outcome == 'snapshot':
            sample = require_value(recovery.sample)
            assert sample.root == recovery.sample_root and sample.token == recovery.sample_token
            assert sample.root.exists()
            samples.cleanup_sandbox_sample(sample)
        elif outcome == 'partial':
            assert recovery.sample is None and recovery.sample_token is not None
            partial_root = require_value(recovery.sample_root)
            # 仅本测试创建、从未交给容器的故障现场，定点清理固定文件和空目录。
            (partial_root / 'example.txt').unlink()
            partial_root.rmdir()
            partial_root.parent.rmdir()
        else:
            assert recovery.sample is None and recovery.sample_root is None
    asyncio.run(scenario())


def test_invalid_plan_does_not_launch_preparation(monkeypatch):
    async def forbidden(*args):
        pytest.fail('invalid plan reached worker')
    monkeypatch.setattr(service, '_prepare_in_thread', forbidden)
    invalid = VerificationRequest.model_construct(plan_id='other')
    with pytest.raises(ValueError):
        asyncio.run(service.prepare_task_verification_input(
            request=invalid, context=None, bindings=None,  # pyright: ignore[reportArgumentType] -- 不允许启动准备线程的非法计划反例
        ))


@pytest.mark.parametrize('partial', [False, True])
def test_preparation_failure_without_cancel_preserves_original_error(monkeypatch, tmp_path, partial):
    error = (
        samples.SandboxSampleCreationUnconfirmed(token='owned', root=tmp_path / 'partial')
        if partial else TaskCommandSourceUnavailable()
    )
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(service, 'create_task_verification_snapshot', fail)
    with pytest.raises(type(error)) as caught:
        asyncio.run(service.prepare_task_verification_input(
            request=VerificationRequest(plan_id='sample_unittest_v1'),
            context=None, bindings=None,  # pyright: ignore[reportArgumentType] -- 工厂替身验证原异常传播，不读取身份占位值
        ))
    assert caught.value is error
