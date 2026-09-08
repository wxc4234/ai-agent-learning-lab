"""最小 Agent Loop：决策、工具执行、观察与终止条件。"""

import asyncio
from collections.abc import Awaitable, Callable, AsyncIterator
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


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """模型已经决定调用工具，但工具尚未执行。"""

    action: ToolAction


@dataclass(frozen=True, slots=True)
class ToolCallSucceeded:
    """工具执行成功，并产生可回传模型的 Observation。"""

    observation: ToolObservation


@dataclass(frozen=True, slots=True)
class ToolCallFailed:
    """工具调用失败，并产生结构化错误 Observation。"""

    observation: ToolErrorObservation


@dataclass(frozen=True, slots=True)
class AgentLoopCompleted:
    """Agent Loop 到达最终答案或最大步数终态。"""

    result: AgentLoopResult


AgentLoopEvent: TypeAlias = (
    ToolCallStarted | ToolCallSucceeded | ToolCallFailed | AgentLoopCompleted
)


async def stream_agent_loop(
    decide: DecisionMaker,
    *,
    max_steps: int = 5,
) -> AsyncIterator[AgentLoopEvent]:
    """逐步执行 Agent Loop，并在关键节点产生领域事件。"""

    if max_steps < 1:
        raise ValueError("max_steps 必须大于等于 1")

    observations: list[AgentObservation] = []

    for step_number in range(1, max_steps + 1):
        decision = await decide(tuple(observations))

        if isinstance(decision, FinalAnswer):
            yield AgentLoopCompleted(
                result=AgentLoopResult(
                    status="completed",
                    answer=decision.content,
                    steps_taken=step_number,
                    observations=tuple(observations),
                )
            )
            return

        yield ToolCallStarted(action=decision)
        tool_definition = TOOL_REGISTRY.get(decision.tool_name)

        if tool_definition is None:
            error_observation = ToolErrorObservation(
                tool_call_id=decision.tool_call_id,
                tool_name=decision.tool_name,
                code="unknown_tool",
                message=f"工具未注册：{decision.tool_name}",
            )
            observations.append(error_observation)
            yield ToolCallFailed(observation=error_observation)
            continue

        try:
            validated_arguments = tool_definition.validate_arguments(decision.arguments)
        except ValidationError as error:
            error_observation = ToolErrorObservation(
                tool_call_id=decision.tool_call_id,
                tool_name=decision.tool_name,
                code="invalid_tool_arguments",
                message="工具参数未通过校验",
                details=error.json(include_url=False),
            )
            observations.append(error_observation)

            yield ToolCallFailed(observation=error_observation)
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
            error_observation = ToolErrorObservation(
                tool_call_id=decision.tool_call_id,
                tool_name=decision.tool_name,
                code="tool_timeout",
                message="工具执行超时",
                details=(f"timeout_seconds={tool_definition.timeout_seconds:g}"),
            )
            observations.append(error_observation)

            # 新增：产生工具失败事件。
            yield ToolCallFailed(observation=error_observation)
            continue
        # 工具是扩展边界，必须把未知执行器的普通异常转成 Observation；
        # CancelledError 继承 BaseException，不会在这里被吞掉。
        except Exception as error:  # noqa: BLE001
            error_observation = ToolErrorObservation(
                tool_call_id=decision.tool_call_id,
                tool_name=decision.tool_name,
                code="tool_execution_failed",
                message="工具执行失败",
                details=type(error).__name__,
            )
            observations.append(error_observation)

            # 新增：产生工具失败事件。
            yield ToolCallFailed(observation=error_observation)
            continue

        success_observation = ToolObservation(
            tool_call_id=decision.tool_call_id,
            tool_name=decision.tool_name,
            result=tool_result,
        )
        observations.append(success_observation)
        yield ToolCallSucceeded(observation=success_observation)

    yield AgentLoopCompleted(
        result=AgentLoopResult(
            status="max_steps_exceeded",
            answer=None,
            steps_taken=max_steps,
            observations=tuple(observations),
        )
    )


# ===== 本课新增：兼容原有调用方式 =====


async def run_agent_loop(
    decide: DecisionMaker,
    *,
    max_steps: int = 5,
) -> AgentLoopResult:
    """消费 Agent 事件流，并返回原有的最终结果。"""

    async for event in stream_agent_loop(
        decide,
        max_steps=max_steps,
    ):
        if isinstance(event, AgentLoopCompleted):
            return event.result

    # 按照事件协议，生成器必须产生 AgentLoopCompleted。
    # 这里防止未来修改生成器时遗漏终态。
    raise RuntimeError("Agent Loop 未产生终态事件")
