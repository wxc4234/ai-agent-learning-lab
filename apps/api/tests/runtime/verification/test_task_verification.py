"""Task编排接线与恢复传播；纯适配替身不代替真实Docker专项。"""

import asyncio
from dataclasses import replace

import pytest

from app.services.runtime.sandbox.sandbox_sample_command import (
    SampleCommandCancelled, SampleCommandRecovery, SampleCommandUnconfirmed,
)
from app.services.runtime.verification import task_verification as service
from app.services.runtime.verification.contracts import VerificationRequest, build_verification_command
from app.services.runtime.verification.sandbox_verification import VerificationAdaptationError
from app.services.workspace.samples.task_sample_binding import TaskSampleBindings
from app.tools.context import ToolExecutionContext
from tests.runtime.verification.test_sandbox_verification import receipt

CONTEXT = ToolExecutionContext(user_id=1, conversation_id='c', workspace_id='w', task_id='t')


def run(request=None):
    return service.run_task_verification(
        request=VerificationRequest(plan_id='sample_unittest_v1') if request is None else request,
        context=CONTEXT, bindings=TaskSampleBindings(),
    )


@pytest.mark.parametrize('changes,outcome', [
    ({}, 'passed'), ({'exit_code': 1}, 'failed'), ({'stdout': ''}, 'unconfirmed'),
    ({'stdout_truncated': True}, 'unconfirmed'),
])
def test_fixed_plan_single_factory_and_adaptation(monkeypatch, changes, outcome):
    calls = []
    sample = object()
    original = receipt(**changes)
    def prepare(**kwargs):
        calls.append('prepare')
        assert kwargs['context'] is CONTEXT and isinstance(kwargs['bindings'], TaskSampleBindings)
        return sample
    async def execute(**kwargs):
        assert kwargs['prepare_in_thread'] is True
        assert kwargs['request'] == build_verification_command('sample_unittest_v1')
        assert kwargs['prepare_sample']() is sample
        calls.append('execute')
        return original
    monkeypatch.setattr(service, 'create_task_verification_snapshot', prepare)
    monkeypatch.setattr(service, '_run_owned_sample_command', execute)
    result = asyncio.run(run())
    assert calls == ['prepare', 'execute']
    assert result.execution is original and result.verification.outcome == outcome


@pytest.mark.parametrize('stage', ['preparing', 'executing', 'cleaning_container', 'cleaning_sample'])
@pytest.mark.parametrize('cancelled', [False, True])
def test_recovery_propagates_unchanged_without_adaptation(monkeypatch, stage, cancelled):
    recovery = SampleCommandRecovery(
        execution_token='a' * 32, container_name='owned', argv=('/usr/local/bin/python',),
        working_directory='.', phase=stage, command=receipt().command if stage.startswith('cleaning') else None,
    )
    error = (SampleCommandCancelled if cancelled else SampleCommandUnconfirmed)(recovery=recovery)
    calls = []
    async def execute(**kwargs):
        calls.append('execute')
        raise error
    monkeypatch.setattr(service, '_run_owned_sample_command', execute)
    monkeypatch.setattr(service, 'adapt_sample_verification', lambda *a: pytest.fail('must not adapt'))
    with pytest.raises(type(error)) as caught:
        asyncio.run(run())
    assert caught.value is error and caught.value.recovery is recovery
    assert calls == ['execute']


def test_adaptation_failure_keeps_execution_receipt(monkeypatch):
    # 有效报告与未退出状态矛盾，不能悄悄丢弃报告或重跑。
    original = receipt()
    inconsistent = replace(original, command=original.command.model_copy(update={'status': 'cancelled'}))
    async def execute(**kwargs):
        return inconsistent
    monkeypatch.setattr(service, '_run_owned_sample_command', execute)
    with pytest.raises(VerificationAdaptationError) as caught:
        asyncio.run(run())
    assert caught.value.execution is inconsistent


def test_invalid_plan_before_side_effects(monkeypatch):
    async def forbidden(**kwargs):
        pytest.fail('invalid plan reached execution')
    monkeypatch.setattr(service, '_run_owned_sample_command', forbidden)
    with pytest.raises(ValueError):
        asyncio.run(run(VerificationRequest.model_construct(plan_id='other')))
