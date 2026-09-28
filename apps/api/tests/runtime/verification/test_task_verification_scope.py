"""真实授权和准备线程进入生命周期，拒绝路径不允许调用Docker。"""

import asyncio
from dataclasses import replace
from threading import Event

import pytest

from app.services.runtime.command.task_command_source import TaskCommandSource
from app.services.runtime.sandbox import sandbox_sample as samples
from app.services.runtime.sandbox import sandbox_sample_command as owner
from app.services.runtime.verification import task_verification as service
from app.services.runtime.verification.contracts import VerificationRequest
from tests.assertions import require_value
from tests.runtime.command.test_task_command_source import lab as lab  # noqa: PLC0414
from tests.runtime.verification.test_task_verification_input import context
from tests.tasks.test_task_deletion_service import target as target  # noqa: PLC0414
from tests.workspace.samples.test_task_sample_binding import setup as setup  # noqa: PLC0414


@pytest.mark.parametrize('kind', ['wrong_user', 'wrong_workspace', 'wrong_task', 'missing', 'busy', 'sealed'])
def test_auth_or_borrow_rejection_never_creates_container(lab, target, monkeypatch, kind):
    bindings, scope, _ = lab
    expected = context(target)
    if kind != 'missing':
        bindings.bind(**scope)
    if kind == 'wrong_user':
        expected = replace(expected, user_id=target['other_id'])
    elif kind.startswith('wrong_'):
        expected = replace(expected, **{kind.removeprefix('wrong_') + '_id': 'f' * 32})
    elif kind == 'sealed':
        with pytest.raises(RuntimeError), bindings.borrow(**scope):
            raise RuntimeError('seal fixture')
    async def forbidden(**kwargs):
        pytest.fail('rejected preparation reached Docker')
    monkeypatch.setattr(owner, 'create_sandbox_container', forbidden)
    def invoke():
        with pytest.raises(owner.SampleCommandUnconfirmed) as caught:
            asyncio.run(service.run_task_verification(
                request=VerificationRequest(plan_id='sample_unittest_v1'), context=expected, bindings=bindings,
            ))
        recovery = caught.value.recovery
        assert not recovery.create_attempted and recovery.sample is None
        assert recovery.phase == 'preparing'
    if kind == 'busy':
        with bindings.borrow(**scope):
            invoke()
    else:
        invoke()
    assert not any(binding.busy for binding in bindings._bindings.values())


def test_cancel_during_real_borrow_keeps_late_snapshot_without_docker(lab, target, monkeypatch):
    bindings, scope, _ = lab
    bindings.bind(**scope)
    expected = context(target)
    entered, release = Event(), Event()
    original = TaskCommandSource.read_sample_bytes
    def read(self):
        content = original(self)
        entered.set()
        assert release.wait(5)
        return content
    monkeypatch.setattr(TaskCommandSource, 'read_sample_bytes', read)
    async def forbidden(**kwargs):
        pytest.fail('cancelled preparation reached Docker')
    monkeypatch.setattr(owner, 'create_sandbox_container', forbidden)
    async def scenario():
        task = asyncio.create_task(service.run_task_verification(
            request=VerificationRequest(plan_id='sample_unittest_v1'), context=expected, bindings=bindings,
        ))
        try:
            async def wait_entered():
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(wait_entered(), 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(owner.SampleCommandCancelled) as caught:
            await task
        recovery = caught.value.recovery
        sample = require_value(recovery.sample)
        assert not recovery.create_attempted and recovery.phase == 'preparing'
        assert bindings.read_status(**scope).status == 'ready'
        assert samples.confirm_sandbox_sample_source(sample)
        # 已确认没有create尝试，本测试所有者显式释放快照。
        samples.cleanup_sandbox_sample(sample)
    asyncio.run(scenario())
