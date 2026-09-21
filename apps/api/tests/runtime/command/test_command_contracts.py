"""命令契约的输入边界与结果组合；不启动进程或连接数据库。"""

import json

import pytest
from pydantic import ValidationError

from app.services.runtime.command.command_contracts import CommandRequest, CommandResult


@pytest.mark.parametrize("argv", [
    None, "git status", (), [], [1], [True], [None], [b"git"],
    [""], [" \t"], ["gi\x00t"], ["git", "a\x00b"],
    ["x" * 4097], ["git"] * 65, ["x" * 4096] * 4 + ["x"],
])
def test_invalid_argv(argv):
    with pytest.raises(ValidationError):
        CommandRequest(argv=argv)


@pytest.mark.parametrize("argv", [
    ["git"], ["git", ""], ["git"] * 64, ["中" * 4096] * 4,
    ["git", "show", "HEAD:notes with spaces.txt"],
    ["program", " a ", "*", "$HOME", "a;b", "a\nb"],
    ["sh", "-c", "echo example"],
])
def test_parameters_are_preserved_without_execution_authorization(argv):
    # Shell字符及解释器请求在语法层可以合法，不将该模型误当成允许列表。
    request = CommandRequest(argv=argv)
    assert request.argv == argv
    assert request.working_directory == "."
    assert CommandRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("path", [
    None, 1, True, "", "x" * 4097, "/tmp", "C:/tmp", "C:tmp",
    "\\tmp", "\\\\server\\share", "a\\b", "a\x00b", "..", "a/../b",
    "a/..", "a:b", "a?b", "a*b", "a<b", 'a"b', "a|b", "a>b",
    "a\nb", "a\tb", "a ", "a.", "CON", "aux.txt", "a/LPT1",
])
def test_invalid_working_directory(path):
    with pytest.raises(ValidationError):
        CommandRequest(argv=["git"], working_directory=path)


@pytest.mark.parametrize("path", [
    ".", "./", "src", "a//b", "a/./b", "中文 目录", ".hidden", "a..b", "x" * 4096,
])
def test_directory_validation_is_lexical_and_preserves_input(path):
    assert CommandRequest(argv=["git"], working_directory=path).working_directory == path


@pytest.mark.parametrize("name", [
    "env", "timeout_seconds", "user_id", "workspace_id", "task_id",
    "context", "root_path", "shell", "max_output_bytes", "status",
])
def test_request_cannot_override_server_policy(name):
    with pytest.raises(ValidationError):
        CommandRequest.model_validate({"argv": ["git"], name: True})


def test_missing_argv_and_public_schema():
    with pytest.raises(ValidationError):
        CommandRequest()
    schema = CommandRequest.model_json_schema()
    assert set(schema["properties"]) == {"argv", "working_directory"}
    assert schema["additionalProperties"] is False
    # JSON入口同样不能借宽松类型转换传入布尔或数字参数。
    with pytest.raises(ValidationError):
        CommandRequest.model_validate_json(json.dumps({"argv": [True]}))


@pytest.mark.parametrize("code", [0, 1, -9])
def test_exited_includes_nonzero_and_signal_codes(code):
    result = CommandResult(status="exited", exit_code=code, duration_ms=0)
    assert result.status == "exited"
    assert result.exit_code == code


@pytest.mark.parametrize("status", ["timed_out", "cancelled"])
@pytest.mark.parametrize("code", [None, 0, 1, -15])
def test_interrupted_result_can_preserve_actual_exit_code(status, code):
    result = CommandResult(status=status, exit_code=code, duration_ms=10)
    assert result.exit_code == code


@pytest.mark.parametrize("code", [
    "working_directory_unavailable", "executable_unavailable", "permission_denied",
    "sandbox_unavailable", "process_start_failed",
])
def test_start_failure_has_fixed_category_and_no_process_output(code):
    result = CommandResult(status="start_failed", start_error_code=code, duration_ms=0)
    assert result.exit_code is None
    assert result.stdout == result.stderr == ""
    assert not result.stdout_truncated and not result.stderr_truncated


@pytest.mark.parametrize("patch", [
    {"start_error_code": None}, {"exit_code": 0}, {"exit_code": -9},
    {"stdout": "data"}, {"stderr": "PRIVATE"},
    {"stdout_truncated": True}, {"stderr_truncated": True},
])
def test_inconsistent_start_failure_rejected(patch):
    values = {"status": "start_failed", "start_error_code": "process_start_failed", "duration_ms": 0}
    with pytest.raises(ValidationError):
        CommandResult.model_validate(values | patch)


@pytest.mark.parametrize("status", ["exited", "timed_out", "cancelled"])
def test_started_command_cannot_have_start_error(status):
    with pytest.raises(ValidationError):
        CommandResult(status=status, exit_code=1, duration_ms=0, start_error_code="permission_denied")


@pytest.mark.parametrize("patch", [
    {"exit_code": None}, {"exit_code": True}, {"exit_code": "0"}, {"exit_code": 1.0},
    {"duration_ms": -1}, {"duration_ms": True}, {"duration_ms": "1"}, {"duration_ms": 1.0},
    {"stdout": b"data"}, {"stderr": None}, {"stdout_truncated": 1}, {"stderr_truncated": "false"},
    {"status": "success"}, {"start_error_code": "PRIVATE"}, {"extra": True},
])
def test_strict_result_fields(patch):
    with pytest.raises(ValidationError):
        CommandResult.model_validate({"status": "exited", "exit_code": 0, "duration_ms": 0} | patch)


@pytest.mark.parametrize("field", ["status", "duration_ms"])
def test_required_result_fields(field):
    values = {"status": "exited", "exit_code": 0, "duration_ms": 0}
    del values[field]
    with pytest.raises(ValidationError):
        CommandResult.model_validate(values)


@pytest.mark.parametrize("field", ["stdout", "stderr"])
def test_per_stream_output_character_boundary(field):
    # 多字节字符证明这里限制的是字符，不是实际捕获的原始字节。
    text = "中" * 65_536
    result = CommandResult(status="exited", exit_code=0, duration_ms=0, **{field: text})
    assert getattr(result, field) == text
    with pytest.raises(ValidationError):
        CommandResult(status="exited", exit_code=0, duration_ms=0, **{field: text + "x"})


@pytest.mark.parametrize("stdout_truncated,stderr_truncated", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_independent_truncation_and_json_round_trip(stdout_truncated, stderr_truncated):
    result = CommandResult(
        status="exited", exit_code=1, duration_ms=5,
        stdout="stdout", stderr="stderr",
        stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
    )
    restored = CommandResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.stdout_truncated is stdout_truncated
    assert restored.stderr_truncated is stderr_truncated
    with pytest.raises(ValidationError):
        result.exit_code = 0


@pytest.mark.parametrize("oom", [None, False, True])
@pytest.mark.parametrize("daemon_error", [None, False, True])
@pytest.mark.parametrize("code", [0, 7])
def test_success_requires_known_negative_error_facts(oom, daemon_error, code):
    result = CommandResult(status="exited", exit_code=code, duration_ms=0,
                           oom_killed=oom, daemon_error=daemon_error)
    assert result.succeeded is (code == 0 and oom is False and daemon_error is False)
    restored = CommandResult.model_validate_json(result.model_dump_json())
    assert restored == result and restored.succeeded is result.succeeded
    assert "succeeded" not in result.model_dump()


@pytest.mark.parametrize("status", ["timed_out", "cancelled"])
def test_interruption_is_not_success_even_with_clean_exit(status):
    result = CommandResult(status=status, exit_code=0, duration_ms=1,
                           oom_killed=False, daemon_error=False)
    assert not result.succeeded


@pytest.mark.parametrize("field", ["oom_killed", "daemon_error"])
@pytest.mark.parametrize("value", [0, 1, "false", "true", 0.0])
def test_exit_error_facts_are_strict_booleans(field, value):
    with pytest.raises(ValidationError):
        CommandResult(status="exited", exit_code=0, duration_ms=0, **{field: value})


@pytest.mark.parametrize("field", ["oom_killed", "daemon_error"])
@pytest.mark.parametrize("value", [False, True])
def test_start_failure_cannot_claim_observed_exit_facts(field, value):
    with pytest.raises(ValidationError):
        CommandResult(status="start_failed", start_error_code="process_start_failed",
                      duration_ms=0, **{field: value})


def test_absent_exit_facts_stay_unknown_and_success_cannot_be_supplied():
    result = CommandResult(status="exited", exit_code=0, duration_ms=0)
    assert result.oom_killed is None and result.daemon_error is None
    assert not result.succeeded
    with pytest.raises(ValidationError):
        CommandResult.model_validate(result.model_dump() | {"succeeded": True})
    with pytest.raises(ValidationError):
        result.oom_killed = False
