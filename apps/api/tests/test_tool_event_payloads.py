import pytest

from app.services.agent_runtime import ToolErrorObservation, ToolObservation
from app.services.tool_event_payloads import (
    build_tool_call_error_payload,
    build_tool_call_result_payload,
)


def test_build_tool_call_result_payload_includes_execution_duration():
    observation = ToolObservation(
        tool_call_id="call-area",
        tool_name="calculate_rectangle_area",
        result="12",
        duration_ms=7,
    )

    assert build_tool_call_result_payload(observation) == {
        "tool_call_id": "call-area",
        "tool_name": "calculate_rectangle_area",
        "result": "12",
        "duration_ms": 7,
    }


@pytest.mark.parametrize("duration_ms", [None, -1, True, 1.5])
def test_build_tool_call_result_payload_rejects_invalid_duration(duration_ms):
    observation = ToolObservation(
        tool_call_id="call-area",
        tool_name="calculate_rectangle_area",
        result="12",
        duration_ms=duration_ms,
    )

    with pytest.raises(ValueError, match="TOOL_CALL_RESULT"):
        build_tool_call_result_payload(observation)


@pytest.mark.parametrize(
    ("code", "duration_ms"),
    [
        ("tool_execution_failed", 7),
        ("tool_timeout", 11),
    ],
)
def test_build_tool_call_error_payload_includes_execution_duration(
    code,
    duration_ms,
):
    observation = ToolErrorObservation(
        tool_call_id="call-failed",
        tool_name="unstable_tool",
        code=code,
        message="工具执行失败",
        details="RuntimeError",
        duration_ms=duration_ms,
    )

    assert build_tool_call_error_payload(observation) == {
        "tool_call_id": "call-failed",
        "tool_name": "unstable_tool",
        "code": code,
        "message": "工具执行失败",
        "duration_ms": duration_ms,
        "details": "RuntimeError",
    }


@pytest.mark.parametrize("code", ["unknown_tool", "invalid_tool_arguments"])
def test_build_tool_call_error_payload_preserves_pre_execution_null(code):
    observation = ToolErrorObservation(
        tool_call_id="call-invalid",
        tool_name="invalid_tool",
        code=code,
        message="工具调用无效",
        duration_ms=None,
    )

    assert build_tool_call_error_payload(observation)["duration_ms"] is None


def test_build_tool_call_error_payload_rejects_duration_before_execution():
    observation = ToolErrorObservation(
        tool_call_id="call-unknown",
        tool_name="unknown_tool",
        code="unknown_tool",
        message="工具未注册",
        duration_ms=1,
    )

    with pytest.raises(ValueError, match="执行前工具错误"):
        build_tool_call_error_payload(observation)


@pytest.mark.parametrize("duration_ms", [None, -1, True, 1.5])
def test_build_tool_call_error_payload_rejects_invalid_execution_duration(
    duration_ms,
):
    observation = ToolErrorObservation(
        tool_call_id="call-failed",
        tool_name="unstable_tool",
        code="tool_execution_failed",
        message="工具执行失败",
        duration_ms=duration_ms,
    )

    with pytest.raises(ValueError, match="TOOL_CALL_ERROR"):
        build_tool_call_error_payload(observation)
