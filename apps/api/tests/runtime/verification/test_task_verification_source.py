"""原始数据不能变成测试代码；仅运行固定模板，不执行项目文件。"""

import ast
import base64
import subprocess
import sys

import pytest

from app.services.runtime.sandbox.sandbox_sample import MAX_SANDBOX_SNAPSHOT_BYTES
from app.services.runtime.verification.task_verification_input import (
    MAX_TASK_VERIFICATION_BYTES, build_task_verification_source,
)


@pytest.mark.parametrize('content', [
    b'', b'old\n', b'new\n', bytes(range(256)), b'\xef\xbb\xbfnew\r\n',
    b'"); __import__("os")._exit(0); #', b'"""\nraise SystemExit(0)\n#',
    b'x' * MAX_TASK_VERIFICATION_BYTES,
])
def test_content_roundtrips_only_as_literal_data(content):
    source = build_task_verification_source(content)
    tree = ast.parse(source)
    assignment = tree.body[2]
    assert isinstance(assignment, ast.Assign)
    call = assignment.value
    assert isinstance(call, ast.Call) and isinstance(call.args[0], ast.Constant)
    encoded = call.args[0].value
    assert isinstance(encoded, str)
    assert base64.b64decode(encoded, validate=True) == content
    assert len(source) <= MAX_SANDBOX_SNAPSHOT_BYTES
    assert isinstance(tree.body[3], ast.ClassDef)
    assert len(tree.body) == 4


@pytest.mark.parametrize('content', [None, 'new\n', bytearray(b'new\n'), b'x' * (MAX_TASK_VERIFICATION_BYTES + 1)])
def test_invalid_or_oversized_data_rejected(content):
    with pytest.raises(ValueError, match='task_verification_input_unavailable'):
        build_task_verification_source(content)


@pytest.mark.parametrize('content,code', [
    (b'new\n', 0), (b'old\n', 1), (b'new\r\n', 1), (b'\xef\xbb\xbfnew\n', 1),
    (b'raise SystemExit(0)', 1), (b'"); __import__("os")._exit(0); #', 1),
])
def test_fixed_expectation_runs_without_executing_input(content, code):
    source = build_task_verification_source(content) + b'\nunittest.main()\n'
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c', source.decode('ascii')],
        capture_output=True, timeout=5, check=False,
    )
    assert result.returncode == code
    assert b'Ran 1 test' in result.stderr
