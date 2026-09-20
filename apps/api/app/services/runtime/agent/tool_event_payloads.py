"""把 Runtime 工具观察映射为稳定的公共流事件 Payload。"""

from typing import Final

from app.services.runtime.agent.agent_runtime import (
    ToolErrorObservation,
    ToolObservation,
)


# 本课新增：这两类错误已经真正进入工具执行器。
_EXECUTION_ERROR_CODES: Final = frozenset(
    {
        "tool_execution_failed",
        "tool_timeout",
    }
)

# 本课新增：这两类错误发生在调用执行器之前。
_PRE_EXECUTION_ERROR_CODES: Final = frozenset(
    {
        "unknown_tool",
        "invalid_tool_arguments",
    }
)


def _require_duration_ms(
    duration_ms: int | None,
    *,
    event_name: str,
) -> int:
    """读取必须存在的非负整数耗时。"""

    if duration_ms is None:
        raise ValueError(f"{event_name} 缺少工具执行耗时")

    # bool 是 int 的子类，因此需要显式排除。
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or duration_ms < 0
    ):
        raise ValueError(f"{event_name} 的工具执行耗时必须是非负整数")

    return duration_ms


def _read_error_duration_ms(
    observation: ToolErrorObservation,
) -> int | None:
    """根据错误发生阶段确定公开协议中的耗时。"""

    if observation.code in _EXECUTION_ERROR_CODES:
        return _require_duration_ms(
            observation.duration_ms,
            event_name="TOOL_CALL_ERROR",
        )

    if observation.code in _PRE_EXECUTION_ERROR_CODES:
        if observation.duration_ms is not None:
            raise ValueError("执行前工具错误不能包含工具执行耗时")

        # 必须显式返回 None，使 JSON 中出现 duration_ms: null。
        return None

    # 类型提示只在静态检查阶段生效，运行时仍保护公共协议。
    raise ValueError(f"无法识别的工具错误代码：{observation.code}")


def build_tool_call_result_payload(
    observation: ToolObservation,
) -> dict[str, object]:
    """构造工具成功事件的公共 Payload。"""

    return {
        "tool_call_id": observation.tool_call_id,
        "tool_name": observation.tool_name,
        "result": observation.result,
        "duration_ms": _require_duration_ms(
            observation.duration_ms,
            event_name="TOOL_CALL_RESULT",
        ),
    }


def build_tool_call_error_payload(
    observation: ToolErrorObservation,
) -> dict[str, object]:
    """构造工具失败事件的公共 Payload。"""

    payload: dict[str, object] = {
        "tool_call_id": observation.tool_call_id,
        "tool_name": observation.tool_name,
        "code": observation.code,
        "message": observation.message,
        "duration_ms": _read_error_duration_ms(observation),
    }

    if observation.details is not None:
        payload["details"] = observation.details

    return payload
