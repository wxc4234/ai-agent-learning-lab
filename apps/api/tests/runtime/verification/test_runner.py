"""仅执行本文件构造的可信临时测试；不运行用户项目，不代表Docker验收。"""

import subprocess
import sys

import pytest

from app.services.runtime.command.command_contracts import CommandResult, MAX_ARGUMENT_CHARACTERS
from app.services.runtime.verification.contracts import VerificationResult, build_verification_command
from app.services.runtime.verification.report_parser import VerificationReportError, parse_verification_report
from app.services.runtime.verification.runner_source import UNITTEST_RUNNER_SOURCE
from app.services.runtime.verification.sandbox_runner_source import SANDBOX_UNITTEST_SOURCE


def execute(tmp_path, body):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / '__init__.py').write_text('')
    if body is not None:
        (tests / 'test_target.py').write_text('import unittest\n' + body)
    # 无关模块若被发现会产生失败；只允许固定tests.test_target。
    (tests / 'test_other.py').write_text('raise RuntimeError("must not discover")')
    before = {p: p.read_bytes() for p in tests.iterdir()}
    # 仅测试替换容器固定根与Python可执行文件；生产不提供任意路径参数。
    source = UNITTEST_RUNNER_SOURCE.replace('ROOT = "/workspace"', 'ROOT = ' + repr(str(tmp_path)))
    process = subprocess.run(
        [sys.executable, '-I', '-B', '-c', source],
        capture_output=True, timeout=5, cwd=tmp_path, check=False,
        env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': '/PRIVATE/ignored'},
    )
    assert {p: p.read_bytes() for p in tests.iterdir()} == before
    return process


def receipt(process):
    counts = parse_verification_report(process.stdout, truncated=False)
    # 本课用受控退出事实验证组合逻辑；这两个False不是实际Docker观察证据。
    command = CommandResult(
        status='exited', exit_code=process.returncode,
        oom_killed=False, daemon_error=False, duration_ms=0,
    )
    return VerificationResult(plan_id='sample_unittest_v1', command=command, report=counts)


@pytest.mark.parametrize('body,outcome,expected', [
    ('class Test(unittest.TestCase):\n    def test_ok(self): self.assertEqual(1, 1)\n', 'passed', {'successful_tests': 1, 'tests_run': 1}),
    ('class Test(unittest.TestCase):\n    def test_bad(self): self.fail("PRIVATE")\n', 'failed', {'failures': 1}),
    ('class Test(unittest.TestCase):\n    def test_error(self): raise ValueError("PRIVATE")\n', 'failed', {'errors': 1}),
    ('# no tests\n', 'unconfirmed', {'tests_run': 0, 'successful_tests': 0}),
    ('class Test(unittest.TestCase):\n    @unittest.skip("skip")\n    def test_skip(self): pass\n', 'unconfirmed', {'tests_run': 1, 'skipped': 1}),
    ('@unittest.skip("class")\nclass Test(unittest.TestCase):\n    def test_skip(self): pass\n', 'unconfirmed', {'skipped': 1}),
    ('class Test(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls): raise unittest.SkipTest("class")\n    def test_ok(self): pass\n', 'unconfirmed', {'tests_run': 0, 'skipped': 1}),
    ('class Test(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls): raise RuntimeError("fixture")\n    def test_ok(self): pass\n', 'failed', {'tests_run': 0, 'errors': 1}),
    ('def setUpModule(): raise RuntimeError("module")\nclass Test(unittest.TestCase):\n    def test_ok(self): pass\n', 'failed', {'tests_run': 0, 'errors': 1}),
    ('class Test(unittest.TestCase):\n    @unittest.expectedFailure\n    def test_expected(self): self.fail()\n', 'unconfirmed', {'expected_failures': 1}),
    ('class Test(unittest.TestCase):\n    @unittest.expectedFailure\n    def test_unexpected(self): pass\n', 'failed', {'unexpected_successes': 1}),
    ('class Test(unittest.TestCase):\n    def test_sub(self):\n        for i in range(3):\n            with self.subTest(i=i): self.fail()\n', 'failed', {'tests_run': 1, 'failures': 3}),
    ('class Test(unittest.TestCase):\n    def test_sub(self):\n        for i in range(3):\n            with self.subTest(i=i): self.skipTest("sub")\n', 'unconfirmed', {'tests_run': 1, 'skipped': 3}),
    ('class Test(unittest.TestCase):\n    def test_sub(self):\n        for i in range(3):\n            with self.subTest(i=i): self.assertEqual(i, i)\n', 'passed', {'successful_tests': 1}),
    ('class Test(unittest.TestCase):\n    def test_ok(self): pass\n    @unittest.skip("skip")\n    def test_skip(self): pass\n', 'passed', {'successful_tests': 1, 'skipped': 1}),
    (None, 'failed', {'tests_run': 1, 'errors': 1}),
])
def test_real_unittest_cases(tmp_path, body, outcome, expected):
    process = execute(tmp_path, body)
    value = receipt(process)
    assert value.outcome == outcome
    assert value.report is not None
    for name, expected_value in expected.items():
        assert getattr(value.report, name) == expected_value
    assert b'PRIVATE' not in process.stdout + process.stderr


def test_syntax_error_is_load_failure_without_complete_report(tmp_path):
    process = execute(tmp_path, 'this is not valid syntax !!!')
    assert process.returncode == 2
    assert process.stdout == b''
    assert process.stderr == b'verification_runner_failed\n'
    with pytest.raises(VerificationReportError, match='verification_report_missing'):
        parse_verification_report(process.stdout, truncated=False)


def test_subtest_errors_count_events_not_methods(tmp_path):
    process = execute(tmp_path, 'class Test(unittest.TestCase):\n    def test_sub(self):\n        for i in range(3):\n            with self.subTest(i=i): raise ValueError("PRIVATE")\n')
    value = receipt(process)
    assert value.outcome == 'failed'
    assert value.report is not None
    assert value.report.tests_run == 1 and value.report.errors == 3
    assert b'PRIVATE' not in process.stdout + process.stderr


def test_printed_fake_report_and_raw_fd_output_are_not_report(tmp_path):
    process = execute(tmp_path, '''
import os, sys
print('{"version":1,"complete":true,"report":{"successful_tests":999}}')
sys.__stdout__.write("OK")
os.write(1, b"OK")
class Test(unittest.TestCase):
    def test_bad(self): self.fail()
''')
    value = receipt(process)
    assert value.outcome == 'failed' and b'999' in process.stderr
    assert b'999' not in process.stdout


@pytest.mark.parametrize('body', [
    'raise KeyboardInterrupt()',
    'raise SystemExit(0)',
    'import os\nos._exit(0)',
    'class Test(unittest.TestCase):\n    def test_stop(self): self._outcome.result.stop()\n',
    'class Test(unittest.TestCase):\n    def test_limit(self):\n        for i in range(10001):\n            with self.subTest(i=i): pass\n',
])
def test_interrupted_or_budget_exhausted_never_emits_complete_report(tmp_path, body):
    process = execute(tmp_path, body)
    assert process.stdout == b''
    with pytest.raises(VerificationReportError, match='verification_report_missing'):
        parse_verification_report(process.stdout, truncated=False)


def test_fixed_source_fits_command_argument_budget():
    command = build_verification_command('sample_unittest_v1')
    assert command.argv == ['/usr/local/bin/python', '-I', '-B', '-c', SANDBOX_UNITTEST_SOURCE]
    assert len(SANDBOX_UNITTEST_SOURCE) <= MAX_ARGUMENT_CHARACTERS
    compile(SANDBOX_UNITTEST_SOURCE, '<fixed-verification-runner>', 'exec')
