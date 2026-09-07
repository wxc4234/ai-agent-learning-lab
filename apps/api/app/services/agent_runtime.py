"""最小 Agent Loop：决策、工具执行、观察与终止条件。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from pydantic import ValidationError

from app.tools.registry import TOOL_REGISTRY


@dataclass(frozen=True, slots=True)
class ToolAction:
    """模型请求执行一次工具。"""

    tool_call_id: str
    tool_name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class FinalAnswer:
    """模型决定结束运行并返回最终答案。"""

    content: str


AgentDecision: TypeAlias = ToolAction | FinalAnswer


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """工具执行成功后返回给下一轮决策的结果。"""

    tool_call_id: str
    tool_name: str
    result: str


@dataclass(frozen=True, slots=True)
class ToolErrorObservation:
    """工具请求失败后返回给下一轮决策的错误信息。"""

    tool_call_id: str
    tool_name: str
    code: Literal[
        "unknown_tool",
        "invalid_tool_arguments",
        "tool_execution_failed",
        "tool_timeout",
    ]
    message: str
    details: str | None = None


AgentObservation: TypeAlias = ToolObservation | ToolErrorObservation


@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    """Agent Loop 的最终状态。"""

    status: Literal["completed", "max_steps_exceeded"]
    answer: str | None
    steps_taken: int
    observations: tuple[AgentObservation, ...]


DecisionMaker: TypeAlias = Callable[
    [tuple[AgentObservation, ...]],
    Awaitable[AgentDecision],
]


async def run_agent_loop(
    decide: DecisionMaker,
    *,
    max_steps: int = 5,
) -> AgentLoopResult:
    """重复决策和执行工具，直到得到答案或达到步数上限。"""

    if max_steps < 1:
        raise ValueError("max_steps 必须大于等于 1")

    observations: list[AgentObservation] = []

    for step_number in range(1, max_steps + 1):
        decision = await decide(tuple(observations))

        if isinstance(decision, FinalAnswer):
            return AgentLoopResult(
                status="completed",
                answer=decision.content,
                steps_taken=step_number,
                observations=tuple(observations),
            )

        tool_definition = TOOL_REGISTRY.get(decision.tool_name)

        if tool_definition is None:
            observations.append(
                ToolErrorObservation(
                    tool_call_id=decision.tool_call_id,
                    tool_name=decision.tool_name,
                    code="unknown_tool",
                    message=f"工具未注册：{decision.tool_name}",
                )
            )
            continue

        try:
            validated_arguments = tool_definition.validate_arguments(decision.arguments)
        except ValidationError as error:
            observations.append(
                ToolErrorObservation(
                    tool_call_id=decision.tool_call_id,
                    tool_name=decision.tool_name,
                    code="invalid_tool_arguments",
                    message="工具参数未通过校验",
                    details=error.json(include_url=False),
                )
            )
            continue

        try:
            tool_result = await asyncio.wait_for(
                asyncio.to_thread(
                    tool_definition.execute,
                    validated_arguments,
                ),
                timeout=tool_definition.timeout_seconds,
            )
        except asyncio.TimeoutError:
            observations.append(
                ToolErrorObservation(
                    tool_call_id=decision.tool_call_id,
                    tool_name=decision.tool_name,
                    code="tool_timeout",
                    message="工具执行超时",
                    details=(f"timeout_seconds={tool_definition.timeout_seconds:g}"),
                )
            )
            continue
        # 工具是扩展边界，必须把未知执行器的普通异常转成 Observation；
        # CancelledError 继承 BaseException，不会在这里被吞掉。
        except Exception as error:  # noqa: BLE001
            observations.append(
                ToolErrorObservation(
                    tool_call_id=decision.tool_call_id,
                    tool_name=decision.tool_name,
                    code="tool_execution_failed",
                    message="工具执行失败",
                    details=type(error).__name__,
                )
            )
            continue

        observations.append(
            ToolObservation(
                tool_call_id=decision.tool_call_id,
                tool_name=decision.tool_name,
                result=tool_result,
            )
        )

    return AgentLoopResult(
        status="max_steps_exceeded",
        answer=None,
        steps_taken=max_steps,
        observations=tuple(observations),
    )
