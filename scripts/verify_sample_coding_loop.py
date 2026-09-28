"""真实PostgreSQL→审批应用→Git→Docker固定验证；不调用模型、不开放项目写入。

从apps/api运行：../../.venv/bin/python -m pytest ../../scripts/verify_sample_coding_loop.py -q -s
"""

import asyncio

import pytest

from app.services.runtime.verification.contracts import VerificationRequest
from app.services.runtime.verification.task_verification import run_task_verification
from tests.workspace.git.test_task_sample_diff import exercise, fingerprint, loop, sample, setup, target

pytest_plugins = ['tests.conftest']
__all__ = ['loop', 'sample', 'setup', 'target']


@pytest.mark.parametrize('mode', ['approved', 'rejected', 'stale'])
def test_real_coding_loop(loop, mode):
    bindings, scope, root, context = loop
    calls = []
    def verify(expected):
        before = fingerprint(root / 'example.txt')
        result = asyncio.run(run_task_verification(
            request=VerificationRequest(plan_id='sample_unittest_v1'),
            context=context, bindings=bindings,
        ))
        assert result.verification.outcome == expected
        assert result.execution.sample_cleaned
        assert fingerprint(root / 'example.txt') == before
        assert bindings.read_status(**scope).status == 'ready'
        calls.append(expected)
    exercise(loop, mode, verify)
    assert calls == ['failed', 'passed' if mode == 'approved' else 'failed']
    print(f'PASS {mode}: same Task source, real Git/Docker, {calls}, source removed')
