"""固定计划和结果的纯逻辑验收，不启动Git、Docker或测试进程。"""

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from app.services.runtime.command.command_contracts import CommandResult
from app.services.runtime.sandbox.sandbox_spec import build_sandbox_create_spec
from app.services.runtime.verification import contracts as service

PLAN = 'sample_unittest_v1'


def command(**changes):
    return CommandResult.model_validate({
        'status': 'exited', 'exit_code': 0, 'oom_killed': False,
        'daemon_error': False, 'duration_ms': 10, **changes,
    })


def report(**changes):
    # 成功方法数独立于事件计数；默认值仅用于本文件的受控反例组合。
    changes.setdefault('successful_tests', max(0, changes.get('tests_run', 2)
        - sum(changes.get(key, 0) for key in ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes')))
        if type(changes.get('tests_run', 2)) is int else 0)
    return service.VerificationTestReport.model_validate({
        'plan_id': PLAN, 'tests_run': 2, 'failures': 0, 'errors': 0,
        'skipped': 0, 'expected_failures': 0, 'unexpected_successes': 0, **changes,
    })


def result(process=None, counts=None):
    return service.VerificationResult(
        plan_id=PLAN, command=command() if process is None else process, report=counts,
    )


@pytest.mark.parametrize('field', [
    'argv', 'path', 'working_directory', 'env', 'timeout_seconds', 'max_bytes',
    'image', 'user_id', 'workspace_id', 'task_id', 'source',
])
def test_request_forbids_overrides(field):
    with pytest.raises(ValidationError):
        service.VerificationRequest.model_validate({'plan_id': PLAN, field: 'PRIVATE'})


@pytest.mark.parametrize('value', [None, 1, True, {}, [], '', 'pytest', '../test', PLAN + ' ', b'sample_unittest_v1'])
def test_unknown_plan_and_invalid_type(value):
    with pytest.raises(ValueError, match='verification_plan_unavailable'):
        service.resolve_verification_plan(value)
    with pytest.raises(ValidationError):
        service.VerificationRequest.model_validate({'plan_id': value})


def test_required_plan_and_immutable_server_mapping():
    with pytest.raises(ValidationError):
        service.VerificationRequest.model_validate({})
    request = service.VerificationRequest(plan_id=PLAN)
    with pytest.raises(ValidationError):
        request.plan_id = PLAN
    plan = service.resolve_verification_plan(request.plan_id)
    with pytest.raises(FrozenInstanceError):
        setattr(plan, 'timeout_seconds', 0)  # noqa: B010 -- 验证冻结实例的运行时保护
    with pytest.raises(TypeError):
        service._PLANS[PLAN] = plan  # pyright: ignore[reportIndexIssue] -- 反例验证登记不可写


def test_fixed_command_is_fresh_and_fits_existing_sandbox(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('must not execute'))
    plan = service.resolve_verification_plan(PLAN)
    first = service.build_verification_command(PLAN)
    assert first.argv == [
        '/usr/local/bin/python', '-I', '-B', '-c', service.SANDBOX_UNITTEST_SOURCE,
    ]
    first.argv[0] = '/bin/sh'
    second = service.build_verification_command(PLAN)
    assert tuple(second.argv) == plan.argv
    assert second.working_directory == '.'
    spec = build_sandbox_create_spec(request=second, execution_token='a' * 32)
    assert spec.argv[-len(plan.argv):] == plan.argv
    assert plan.image in spec.argv
    assert '--network=none' in spec.argv and '--read-only' in spec.argv
    assert '--workdir=/tmp' in spec.argv
    assert plan.source == 'trusted_sample_snapshot'
    assert plan.timeout_seconds == service.COMMAND_TIMEOUT_SECONDS
    assert plan.max_capture_bytes_per_stream == service.MAX_CAPTURE_BYTES_PER_STREAM


def test_pass_requires_exit_facts_and_non_skipped_test():
    assert result(counts=report()).outcome == 'passed'
    assert result(counts=report(skipped=1)).outcome == 'passed'
    assert result(counts=report(expected_failures=1)).outcome == 'passed'


@pytest.mark.parametrize('changes', [
    {'exit_code': 1}, {'exit_code': -9}, {'oom_killed': True}, {'daemon_error': True},
])
def test_confirmed_command_failure(changes):
    assert result(process=command(**changes)).outcome == 'failed'


@pytest.mark.parametrize('changes', [
    {'oom_killed': None}, {'daemon_error': None},
    {'stdout_truncated': True}, {'stderr_truncated': True},
    {'status': 'timed_out'}, {'status': 'cancelled'},
])
def test_unknown_or_interrupted_never_passes(changes):
    assert result(process=command(**changes)).outcome == 'unconfirmed'


@pytest.mark.parametrize('counts', [None, report(tests_run=0), report(skipped=2), report(expected_failures=2)])
def test_absent_zero_skipped_or_only_expected_failure_report(counts):
    # stdout看似成功也不能替代完整计数。
    assert result(process=command(stdout='Ran 100 tests\nOK'), counts=counts).outcome == 'unconfirmed'


def test_start_failure_preserves_unconfirmed_target():
    process = command(
        status='start_failed', exit_code=None, oom_killed=None, daemon_error=None,
        start_error_code='sandbox_unavailable',
    )
    assert result(process=process).outcome == 'unconfirmed'


@pytest.mark.parametrize('changes', [
    {'tests_run': -1}, {'tests_run': True}, {'tests_run': '2'},
    {'tests_run': service.MAX_TEST_COUNT + 1}, {'successful_tests': 3},
    {'successful_tests': 1, 'expected_failures': 1, 'unexpected_successes': 1},
    {'tests_run': 0, 'successful_tests': 1}, {'successful_tests': -1},
    {'plan_id': 'other'}, {'passed': True},
])
def test_malformed_report_rejected(changes):
    with pytest.raises(ValidationError):
        report(**changes)


def test_subtest_failures_can_exceed_test_methods():
    counts = report(tests_run=1, failures=5, errors=3)
    assert result(process=command(exit_code=1), counts=counts).outcome == 'failed'


@pytest.mark.parametrize('changes', [{'failures': 1}, {'errors': 1}, {'unexpected_successes': 1}])
def test_zero_exit_conflicting_report_rejected(changes):
    with pytest.raises(ValidationError, match='verification_exit_report_conflict'):
        result(counts=report(**changes))


@pytest.mark.parametrize('status', ['timed_out', 'cancelled'])
def test_partial_execution_cannot_claim_complete_report(status):
    with pytest.raises(ValidationError, match='verification_report_not_completed'):
        result(process=command(status=status), counts=report())


def test_nested_constructed_values_revalidated():
    missing_exit = CommandResult.model_construct(status='exited', duration_ms=0, exit_code=None)
    with pytest.raises(ValidationError):
        result(process=missing_exit, counts=report())
    invalid_counts = report().model_copy(update={'tests_run': -1})
    with pytest.raises(ValidationError):
        result(counts=invalid_counts)
    invalid_plan = report().model_copy(update={'plan_id': 'unknown'})
    with pytest.raises(ValidationError):
        result(counts=invalid_plan)


@pytest.mark.parametrize('field', ['stdout', 'stderr'])
def test_utf8_output_budget_and_invalid_encoding(field):
    exact = 'a' * service.MAX_CAPTURE_BYTES_PER_STREAM
    assert result(process=command(**{field: exact}), counts=report()).outcome == 'passed'
    with pytest.raises(ValidationError, match='verification_output_limit'):
        result(process=command(**{field: '中' * 30_000}), counts=report())
    # 即使用model_copy绕过初次校验，结果边界仍重新校验非法Unicode。
    malformed = command().model_copy(update={field: '\ud800'})
    with pytest.raises(ValidationError, match='string_unicode'):
        result(process=malformed, counts=report())


@pytest.mark.parametrize('field,value', [('outcome', 'passed'), ('source', 'user_project'), ('plan_id', 'other')])
def test_result_cannot_override_status_or_source(field, value):
    with pytest.raises(ValidationError):
        service.VerificationResult.model_validate({
            'plan_id': PLAN, 'command': command(), 'report': report(), field: value,
        })


def test_no_mutation_of_supplied_evidence():
    process, counts = command(), report()
    receipt = result(process=process, counts=counts)
    assert receipt.command is not process and receipt.report is not counts
    assert receipt.outcome == 'passed'
    with pytest.raises(ValidationError):
        receipt.command.exit_code = 1
