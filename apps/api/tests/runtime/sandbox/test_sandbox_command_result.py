"""仅适配已确认执行结果；不连接Docker或把异常包装为成功。"""

import asyncio
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.services.runtime.command.command_capture import CapturedCommandStreams
from app.services.runtime.command.command_contracts import CommandResult
from app.services.runtime.command.command_output import CommandOutputBuffer
from app.services.runtime.sandbox.sandbox_command_result import build_command_result
from app.services.runtime.sandbox.sandbox_execution import (
    SandboxExecutionCancelled, SandboxExecutionResult, SandboxExecutionUnconfirmed,
)
from app.services.runtime.sandbox.sandbox_exit import SandboxExitResult
from app.services.runtime.sandbox.sandbox_identity import SandboxContainerIdentity


IDENTITY = SandboxContainerIdentity(
    container_id="a" * 64, container_name="PRIVATE-NAME",
    execution_token="PRIVATE-TOKEN", image="PRIVATE-IMAGE",
)


def execution(code=0, oom=False, daemon_error=False):
    stdout, stderr = CommandOutputBuffer(), CommandOutputBuffer()
    stdout.feed("你好\n".encode())
    stderr.feed(b"warning\n")
    return SandboxExecutionResult(
        streams=CapturedCommandStreams(stdout=stdout.finish(), stderr=stderr.finish()),
        exit=SandboxExitResult(identity=IDENTITY, exit_code=code, oom_killed=oom, daemon_error=daemon_error),
        duration_ms=123,
    )


@pytest.mark.parametrize("code,oom,daemon_error,success", [
    (0, False, False, True), (7, False, False, False), (137, False, False, False),
    (0, True, False, False), (0, False, True, False), (255, True, True, False),
])
def test_mapping_preserves_facts_without_exposing_internal_identity(code, oom, daemon_error, success):
    original = execution(code, oom, daemon_error)
    result = build_command_result(original)
    assert result.status == "exited" and result.exit_code == code
    assert result.oom_killed is oom and result.daemon_error is daemon_error
    assert result.succeeded is success
    assert result.stdout == "你好\n" and result.stderr == "warning\n"
    assert result.duration_ms == 123 and result.start_error_code is None
    assert not result.stdout_truncated and not result.stderr_truncated
    assert set(result.model_dump()) == {
        "status", "exit_code", "oom_killed", "daemon_error", "stdout", "stderr",
        "stdout_truncated", "stderr_truncated", "duration_ms", "start_error_code",
    }
    assert "PRIVATE" not in result.model_dump_json()
    assert CommandResult.model_validate_json(result.model_dump_json()) == result
    assert original == execution(code, oom, daemon_error)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("kind", ["capture", "display", "both"])
def test_each_truncation_source_and_channel_survives(stream, kind):
    buffer = CommandOutputBuffer(max_capture_bytes=2 if kind in ("capture", "both") else 65536,
                                 max_output_characters=1 if kind in ("display", "both") else 65536)
    buffer.feed(b"abcdef")
    original = execution()
    streams = replace(original.streams, **{stream: buffer.finish()})
    result = build_command_result(replace(original, streams=streams))
    assert getattr(result, f"{stream}_truncated") is True
    other = "stderr" if stream == "stdout" else "stdout"
    assert getattr(result, f"{other}_truncated") is False
    assert getattr(result, stream) == buffer.finish().text
    # 输出截断不改变进程退出事实；业务是否完成由调用方另行判断。
    assert result.succeeded


@pytest.mark.parametrize("code", [None, True, False, -1, 256, "0", 0.0])
def test_docker_exit_code_is_strict_and_bounded(code):
    with pytest.raises(ValueError):
        build_command_result(execution(code=code))


@pytest.mark.parametrize("value", [None, {}, "result", RuntimeError("PRIVATE"), asyncio.CancelledError()])
def test_non_result_is_rejected(value):
    with pytest.raises(TypeError):
        build_command_result(value)


@pytest.mark.parametrize("cancel", [False, True])
def test_even_confirmed_stop_does_not_convert_failure_to_completed_result(cancel):
    facts = {
        "execution_token": "PRIVATE-TOKEN",
        "container_id": "a" * 64,
        "start_attempted": True,
        "stop_confirmed": True,
    }
    error = SandboxExecutionCancelled(**facts) if cancel else SandboxExecutionUnconfirmed(**facts, reason="timed_out")
    with pytest.raises(TypeError):
        build_command_result(error)
    assert error.stop_confirmed


@pytest.mark.parametrize("field,value", [
    ("oom_killed", 0), ("daemon_error", "false"),
])
def test_malformed_internal_boolean_not_coerced(field, value):
    original = execution()
    with pytest.raises(ValidationError):
        build_command_result(replace(original, exit=replace(original.exit, **{field: value})))


@pytest.mark.parametrize("duration", [-1, True, "123", 1.2])
def test_invalid_duration_is_not_replaced_or_coerced(duration):
    with pytest.raises(ValidationError):
        build_command_result(replace(execution(), duration_ms=duration))
