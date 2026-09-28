"""验证适配层只组合真实生命周期结果，不吞掉恢复信息。"""

import asyncio
import json

import pytest

from app.services.runtime.command.command_contracts import CommandResult
from app.services.runtime.sandbox.sandbox_cleanup import SandboxCleanupResult
from app.services.runtime.sandbox.sandbox_sample_command import (
    SampleCommandCancelled, SampleCommandRecovery, SampleCommandResult, SampleCommandUnconfirmed,
)
from app.services.runtime.verification import sandbox_verification as service
from app.services.runtime.verification.contracts import VerificationRequest, build_verification_command
from tests.runtime.verification.test_contracts import report


def receipt(**changes):
    frame = json.dumps({'version': 1, 'complete': True, 'report': report().model_dump()}) + '\n'
    command = CommandResult.model_validate({
        'status': 'exited', 'exit_code': 0, 'oom_killed': False, 'daemon_error': False,
        'duration_ms': 1, 'stdout': frame, **changes,
    })
    return SampleCommandResult(
        command=command, sample_token='a' * 32, sample_cleaned=True,
        cleanup=SandboxCleanupResult(execution_token='b' * 32, container_id='c' * 64),
    )


@pytest.mark.parametrize('changes,outcome,error', [
    ({}, 'passed', None),
    ({'exit_code': 1}, 'failed', None),
    ({'stdout': ''}, 'unconfirmed', 'verification_report_missing'),
    ({'stdout': 'OK\n'}, 'unconfirmed', 'verification_report_invalid_format'),
    ({'stdout': '\ufffd\n'}, 'unconfirmed', 'verification_report_invalid_encoding'),
    ({'stdout_truncated': True}, 'unconfirmed', 'verification_report_truncated'),
    ({'stderr_truncated': True}, 'unconfirmed', None),
    ({'oom_killed': None}, 'unconfirmed', None),
    ({'daemon_error': True}, 'failed', None),
    ({'exit_code': 2, 'stdout': ''}, 'failed', 'verification_report_missing'),
])
def test_adaptation(changes, outcome, error):
    original = receipt(**changes)
    result = service.adapt_sample_verification(original)
    assert result.execution is original
    assert result.verification.outcome == outcome
    assert result.report_error == error


def test_conflicting_report_keeps_cleanup_receipt():
    frame = json.dumps({'version': 1, 'complete': True, 'report': report(failures=1).model_dump()}) + '\n'
    original = receipt(stdout=frame)
    with pytest.raises(service.VerificationAdaptationError) as caught:
        service.adapt_sample_verification(original)
    assert caught.value.execution is original


def test_fixed_request_and_owned_snapshot(monkeypatch):
    sentinel = object()
    original = receipt()
    source = b'import unittest'
    def prepare(*, content):
        assert content is source
        return sentinel
    async def run(**kwargs):
        assert kwargs['request'] == build_verification_command('sample_unittest_v1')
        assert kwargs['prepare_in_thread'] is True
        assert kwargs['prepare_sample']() is sentinel
        return original
    monkeypatch.setattr(service, 'create_sandbox_snapshot', prepare)
    monkeypatch.setattr(service, '_run_owned_sample_command', run)
    result = asyncio.run(service.run_sample_verification(
        request=VerificationRequest(plan_id='sample_unittest_v1'), trusted_test_source=source,
    ))
    assert result.execution is original


@pytest.mark.parametrize('cancelled', [False, True])
def test_lifecycle_failure_propagates_same_recovery(monkeypatch, cancelled):
    command = build_verification_command('sample_unittest_v1')
    recovery = SampleCommandRecovery(
        execution_token='a' * 32, container_name='owned', argv=tuple(command.argv),
        working_directory='.', stop_confirmed=True, execution_reason='timed_out',
    )
    error = (SampleCommandCancelled if cancelled else SampleCommandUnconfirmed)(recovery=recovery)
    async def run(**kwargs):
        raise error
    monkeypatch.setattr(service, '_run_owned_sample_command', run)
    with pytest.raises(type(error)) as caught:
        asyncio.run(service.run_sample_verification(
            request=VerificationRequest(plan_id='sample_unittest_v1'), trusted_test_source=b'',
        ))
    assert caught.value is error and caught.value.recovery is recovery


def test_invalid_plan_never_prepares_or_executes(monkeypatch):
    async def run(**kwargs):
        pytest.fail('must reject before side effects')
    monkeypatch.setattr(service, '_run_owned_sample_command', run)
    invalid = VerificationRequest.model_construct(plan_id='other')
    with pytest.raises(ValueError):
        asyncio.run(service.run_sample_verification(request=invalid, trusted_test_source=b''))


@pytest.mark.parametrize('source', ['not bytes', b'x' * (256 * 1024 + 1)])
def test_invalid_source_never_creates_container(monkeypatch, source):
    from app.services.runtime.sandbox import sandbox_sample_command as owner
    async def forbidden(**kwargs):
        pytest.fail('invalid source reached Docker')
    monkeypatch.setattr(owner, 'create_sandbox_container', forbidden)
    with pytest.raises(SampleCommandUnconfirmed) as caught:
        asyncio.run(service.run_sample_verification(
            request=VerificationRequest(plan_id='sample_unittest_v1'), trusted_test_source=source,
        ))
    assert caught.value.recovery.sample is None
    assert not caught.value.recovery.create_attempted


def test_copied_sample_identity_rejected_before_docker(monkeypatch):
    from dataclasses import replace
    from app.services.runtime.sandbox import sandbox_sample_command as owner
    from app.services.runtime.sandbox.sandbox_sample import cleanup_sandbox_sample, create_sandbox_snapshot
    original = create_sandbox_snapshot(content=b'# trusted')
    copied = replace(original)
    monkeypatch.setattr(service, 'create_sandbox_snapshot', lambda **kwargs: copied)
    async def forbidden(**kwargs):
        pytest.fail('unregistered identity reached Docker')
    monkeypatch.setattr(owner, 'create_sandbox_container', forbidden)
    try:
        with pytest.raises(SampleCommandUnconfirmed) as caught:
            asyncio.run(service.run_sample_verification(
                request=VerificationRequest(plan_id='sample_unittest_v1'), trusted_test_source=b'',
            ))
        assert caught.value.recovery.sample is copied
        assert not caught.value.recovery.create_attempted
    finally:
        # 本测试确认未调用create，仅原登记所有者可释放来源。
        cleanup_sandbox_sample(original)
