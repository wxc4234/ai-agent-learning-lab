"""固定验证参数、纯公开投影与独立定义；不执行Docker、数据库或模型。"""

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.services.runtime.sandbox.sandbox_sample_command import (
    SampleCommandCancelled, SampleCommandRecovery, SampleCommandUnconfirmed,
)
from app.services.runtime.verification.contracts import VerificationRequest
from app.services.runtime.verification.sandbox_verification import adapt_sample_verification
from app.tools import task_verification as tool
from app.tools.context import ToolExecutionContext
from app.tools.errors import SafeToolExecutionError
from app.tools.registry import TOOL_REGISTRY, ToolContextRequiredError
from tests.assertions import require_value
from tests.runtime.verification.test_sandbox_verification import receipt
from tests.runtime.verification.test_contracts import report

CONTEXT = ToolExecutionContext(1, 'c', 'w', 't')


def result(**changes):
    return adapt_sample_verification(receipt(**changes))


def with_counts(**changes):
    counts = report(**changes)
    frame = json.dumps({'version': 1, 'complete': True, 'report': counts.model_dump()}) + '\n'
    return result(stdout=frame, exit_code=1 if counts.has_failures else 0)


@pytest.mark.parametrize('arguments', [
    {}, {'plan_id': None}, {'plan_id': 1}, {'plan_id': 'pytest'}, {'plan_id': []},
    *({'plan_id': 'sample_unittest_v1', name: 'PRIVATE'} for name in (
        'argv', 'path', 'source', 'trusted_test_source', 'expected', 'env', 'image',
        'timeout_seconds', 'max_bytes', 'context', 'user_id', 'workspace_id', 'task_id', 'conversation_id',
    )),
])
def test_invalid_arguments_schema_rejection(arguments):
    with pytest.raises(ValidationError):
        tool.TaskVerificationArguments.model_validate(arguments)


def test_definition_schema_context_and_no_registration():
    calls = []
    async def execute(*, request, context):
        calls.append((request, context))
        return result()
    definition = tool.make_task_verification_definition(execute)
    assert definition.arguments_model is VerificationRequest
    schema = definition.arguments_model.model_json_schema()
    assert definition.as_model_tool()['function'].get('parameters') == schema
    assert schema['required'] == ['plan_id'] and schema['additionalProperties'] is False
    assert set(schema['properties']) == {'plan_id'}
    assert definition.requires_context and definition.is_async and definition.timeout_seconds > 0
    assert definition.name not in TOOL_REGISTRY
    request = definition.validate_arguments('{"plan_id":"sample_unittest_v1"}')
    public = json.loads(asyncio.run(definition.execute_async(request, context=CONTEXT)))
    assert public['outcome'] == 'passed' and calls == [(request, CONTEXT)]
    with pytest.raises(ToolContextRequiredError):
        asyncio.run(definition.execute_async(request))
    async def direct_invalid():
        return await require_value(definition.async_executor)(context=CONTEXT, plan_id='sample_unittest_v1', argv=[])
    with pytest.raises(SafeToolExecutionError) as caught:
        asyncio.run(direct_invalid())
    assert caught.value.code == 'verification_request_rejected' and len(calls) == 1
    invalid = VerificationRequest.model_construct(plan_id='other')
    with pytest.raises(SafeToolExecutionError):
        asyncio.run(definition.execute_async(invalid, context=CONTEXT))
    assert len(calls) == 1


@pytest.mark.parametrize('value,outcome,status', [
    (result(), 'passed', 'complete'),
    (with_counts(failures=1), 'failed', 'complete'),
    (with_counts(tests_run=0), 'unconfirmed', 'complete'),
    (with_counts(skipped=2), 'unconfirmed', 'complete'),
    (with_counts(expected_failures=2), 'unconfirmed', 'complete'),
    (result(stdout=''), 'unconfirmed', 'unavailable'),
    (result(stdout='PRIVATE logs\n'), 'unconfirmed', 'unavailable'),
    (result(stdout_truncated=True), 'unconfirmed', 'unavailable'),
    (result(stderr_truncated=True), 'unconfirmed', 'complete'),
    (result(oom_killed=None), 'unconfirmed', 'complete'),
    (result(daemon_error=None), 'unconfirmed', 'complete'),
    (result(exit_code=2, stdout=''), 'failed', 'unavailable'),
])
def test_outcomes_and_report_availability_remain_distinct(value, outcome, status):
    public = json.loads(tool.project_task_verification_result(value))
    assert public['outcome'] == outcome and public['report_status'] == status
    assert (public['counts'] is None) == (status == 'unavailable')
    assert public['scope'] == 'controlled_sample_only'


def test_explicit_allowlist_omits_all_private_data():
    private = '/PRIVATE/host/source.py SECRET trace container-id'
    value = result(stderr=private)
    value = replace(value, execution=replace(value.execution, sample_token=private))
    encoded = tool.project_task_verification_result(value)
    assert 'PRIVATE' not in encoded and 'SECRET' not in encoded and 'container-id' not in encoded
    public = json.loads(encoded)
    assert set(public) == {'source', 'scope', 'plan_id', 'outcome', 'command', 'report_status', 'counts'}
    assert set(public['command']) == {
        'status', 'exit_code', 'oom_killed', 'daemon_error', 'stdout_truncated', 'stderr_truncated', 'duration_ms',
    }
    assert set(public['counts']) == {
        'tests_run', 'successful_tests', 'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes',
    }
    assert len(encoded.encode()) <= tool.MAX_PUBLIC_RESULT_BYTES
    missing = replace(result(stdout=''), report_error=private)
    assert private not in tool.project_task_verification_result(missing)


@pytest.mark.parametrize('kind', ['uncleaned', 'different_command', 'different_counts', 'invalid_counts', 'report_state'])
def test_inconsistent_internal_value_is_safe_failure(kind):
    value = result()
    if kind == 'uncleaned':
        value = replace(value, execution=replace(value.execution, sample_cleaned=False))
    elif kind == 'different_command':
        value = replace(value, execution=replace(value.execution, command=value.execution.command.model_copy(update={'exit_code': 5})))
    elif kind in ('different_counts', 'invalid_counts'):
        counts = require_value(value.verification.report).model_copy(update={'tests_run': 3 if kind == 'different_counts' else -1})
        value = replace(value, verification=value.verification.model_copy(update={'report': counts}))
    else:
        value = replace(value, report_error='PRIVATE')
    with pytest.raises(SafeToolExecutionError) as caught:
        tool.project_task_verification_result(value)
    assert caught.value.code == 'verification_result_unavailable'
    assert 'PRIVATE' not in str(caught.value)


def test_serialized_byte_budget_rejects_whole_result(monkeypatch):
    value = result()
    size = len(tool.project_task_verification_result(value).encode('utf-8'))
    monkeypatch.setattr(tool, 'MAX_PUBLIC_RESULT_BYTES', size)
    assert len(tool.project_task_verification_result(value).encode()) == size
    monkeypatch.setattr(tool, 'MAX_PUBLIC_RESULT_BYTES', size - 1)
    with pytest.raises(SafeToolExecutionError) as caught:
        tool.project_task_verification_result(value)
    assert caught.value.code == 'verification_result_too_large'


@pytest.mark.parametrize('cancelled', [False, True])
def test_lifecycle_exception_and_recovery_are_not_consumed(cancelled):
    recovery = SampleCommandRecovery(execution_token='a' * 32, container_name='PRIVATE', argv=('/python',), working_directory='.')
    failure = (SampleCommandCancelled if cancelled else SampleCommandUnconfirmed)(recovery=recovery)
    async def execute(*, request, context):
        raise failure
    definition = tool.make_task_verification_definition(execute)
    with pytest.raises(type(failure)) as caught:
        asyncio.run(definition.execute_async(VerificationRequest(plan_id='sample_unittest_v1'), context=CONTEXT))
    assert caught.value is failure and caught.value.recovery is recovery
