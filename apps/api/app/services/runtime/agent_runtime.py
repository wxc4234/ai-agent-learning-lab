"""最小 Agent Loop：决策、工具执行、观察与终止条件。"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Literal, TypeAlias

from pydantic import ValidationError

from app.services.runtime.token_budget import evaluate_token_budget
from app.tools.registry import TOOL_REGISTRY


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """一次或多次模型请求产生的 token 用量。"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_hit_input_tokens: int | None = None
    cache_miss_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ToolAction:
    """模型请求执行一次工具。"""

    tool_call_id: str
    tool_name: str
    arguments: str

    # 记录产生该决策的模型调用用量。
    # compare=False 表示它不影响 ToolAction 的业务相等性。
    model_usage: ModelUsage | None = field(
        default=None,
        compare=False,
    )

    # 产生该决策的模型调用耗时。
    model_duration_ms: int | None = field(
        default=None,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class FinalAnswer:
    """模型决定结束运行并返回最终答案。"""

    content: str

    # 记录模型最终回答产生的调用量。
    model_usage: ModelUsage | None = field(
        default=None,
        compare=False,
    )

    # 产生该决策的模型调用耗时。
    model_duration_ms: int | None = field(
        default=None,
        compare=False,
    )


AgentDecision: TypeAlias = ToolAction | FinalAnswer


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """工具执行成功后返回给下一轮决策的结果。"""

    tool_call_id: str
    tool_name: str
    result: str

    # Runtime 等待工具成功返回的耗时
    # compare=False 保持原有 Observation 业务相等性。
    duration_ms: int | None = field(
        default=None,
        compare=False,
    )


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

    # 发生执行异常或超时时的等待耗时
    # 未注册工具和参数错误没有进入执行器，因此保持 None。
    duration_ms: int | None = field(
        default=None,
        compare=False,
    )


AgentObservation: TypeAlias = ToolObservation | ToolErrorObservation


@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    """Agent Loop 的最终状态。"""

    status: Literal[
        "completed",
        "max_steps_exceeded",
        "token_budget_exhausted",
        "token_usage_unknown",
    ]
    answer: str | None
    steps_taken: int
    observations: tuple[AgentObservation, ...]

    # 所有模型步骤的完整累计用量。
    # 任意一步缺少 usage 时，该字段必须为 None。
    model_usage: ModelUsage | None = None

    # 所有成功返回的模型请求耗时之和。
    # 任意一步缺少耗时数据时，该字段必须为 None。
    model_duration_ms: int | None = field(
        default=None,
        compare=False,
    )

    # 所有实际工具执行等待时间之和
    # 没有执行工具时为 0。
    tool_duration_ms: int = field(
        default=0,
        compare=False,
    )


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
    """Agent Loop 到达最终答案或任一种循环终态。"""

    result: AgentLoopResult


AgentLoopEvent: TypeAlias = (
    ToolCallStarted | ToolCallSucceeded | ToolCallFailed | AgentLoopCompleted
)


def _sum_optional_tokens(left: int | None, right: int | None) -> int | None:
    """只有两边都有真实数据时才相加，避免把缺失值伪装成 0。"""

    if left is None or right is None:
        return None

    return left + right


def _merge_model_usage(
    current: ModelUsage | None,
    new_usage: ModelUsage,
) -> ModelUsage:
    """把一次模型调用的用量合并到本轮 Agent 总用量。"""

    if current is None:
        return new_usage

    return ModelUsage(
        input_tokens=current.input_tokens + new_usage.input_tokens,
        output_tokens=current.output_tokens + new_usage.output_tokens,
        total_tokens=current.total_tokens + new_usage.total_tokens,
        cache_hit_input_tokens=_sum_optional_tokens(
            current.cache_hit_input_tokens,
            new_usage.cache_hit_input_tokens,
        ),
        cache_miss_input_tokens=_sum_optional_tokens(
            current.cache_miss_input_tokens,
            new_usage.cache_miss_input_tokens,
        ),
    )


# 把一次工具执行的纳秒间隔转换成整数毫秒
def _elapsed_milliseconds(started_at_ns: int) -> int:
    """计算从指定时间点开始经过的整数毫秒数。"""

    return max(
        0,
        (perf_counter_ns() - started_at_ns) // 1_000_000,
    )


def _sum_tool_duration_ms(
    observations: Sequence[AgentObservation],
) -> int:
    """汇总所有真正进入执行器的工具等待时间。"""

    total_duration_ms = 0

    for observation in observations:
        if observation.duration_ms is not None:
            total_duration_ms += observation.duration_ms

    return total_duration_ms


async def stream_agent_loop(
    decide: DecisionMaker,
    *,
    max_steps: int = 5,
    # None 表示保持原有行为，不启用预算
    max_total_tokens: int | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    """逐步执行 Agent Loop，并在关键节点产生领域事件。"""

    if max_steps < 1:
        raise ValueError("max_steps 必须大于等于 1")

    # 在第一次模型请求前验证配置，避免花费 Token 后才发现配置非法。
    if max_total_tokens is not None:
        evaluate_token_budget(
            0,
            max_total_tokens=max_total_tokens,
        )

    observations: list[AgentObservation] = []

    # 累计每一步模型调用的 usage。
    total_model_usage: ModelUsage | None = None
    model_usage_is_complete = True

    # 累计模型调用耗时。
    total_model_duration_ms = 0
    model_duration_is_complete = True

    for step_number in range(1, max_steps + 1):
        decision = await decide(tuple(observations))

        # 只要有一步缺失 usage，最终就不能声称拥有完整总量。
        if decision.model_usage is None:
            model_usage_is_complete = False
        else:
            total_model_usage = _merge_model_usage(
                total_model_usage,
                decision.model_usage,
            )

        # 任意一步缺失耗时，都不能输出不完整的总耗时。
        if decision.model_duration_ms is None:
            model_duration_is_complete = False
        else:
            total_model_duration_ms += decision.model_duration_ms

        if isinstance(decision, FinalAnswer):
            yield AgentLoopCompleted(
                result=AgentLoopResult(
                    status="completed",
                    answer=decision.content,
                    steps_taken=step_number,
                    observations=tuple(observations),
                    model_usage=(
                        total_model_usage if model_usage_is_complete else None
                    ),
                    model_duration_ms=(
                        total_model_duration_ms if model_duration_is_complete else None
                    ),
                    tool_duration_ms=_sum_tool_duration_ms(
                        observations,
                    ),
                )
            )
            return

        # 只有 ToolAction 需要后续工具执行和下一次模型调用，因此在这里检查预算。
        if max_total_tokens is not None:
            current_total_tokens = (
                total_model_usage.total_tokens
                if model_usage_is_complete and total_model_usage is not None
                else None
            )
            budget_status = evaluate_token_budget(
                current_total_tokens,
                max_total_tokens=max_total_tokens,
            )

            # fail-closed：无法确认 usage 时也不能继续产生未知成本。
            if budget_status != "within_budget":
                yield AgentLoopCompleted(
                    result=AgentLoopResult(
                        status=(
                            "token_budget_exhausted"
                            if budget_status == "exhausted"
                            else "token_usage_unknown"
                        ),
                        answer=None,
                        steps_taken=step_number,
                        observations=tuple(observations),
                        model_usage=(
                            total_model_usage
                            if model_usage_is_complete
                            else None
                        ),
                        model_duration_ms=(
                            total_model_duration_ms
                            if model_duration_is_complete
                            else None
                        ),
                        tool_duration_ms=_sum_tool_duration_ms(
                            observations,
                        ),
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

        # 只从真正调用执行器之前开始计时
        tool_started_at_ns = perf_counter_ns()

        try:
            tool_result = await asyncio.wait_for(
                asyncio.to_thread(
                    tool_definition.execute,
                    validated_arguments,
                ),
                timeout=tool_definition.timeout_seconds,
            )
        except asyncio.TimeoutError:
            # 超时时也记录 Runtime 等待时间
            tool_duration_ms = _elapsed_milliseconds(tool_started_at_ns)

            error_observation = ToolErrorObservation(
                tool_call_id=decision.tool_call_id,
                tool_name=decision.tool_name,
                code="tool_timeout",
                message="工具执行超时",
                details=(f"timeout_seconds={tool_definition.timeout_seconds:g}"),
                duration_ms=tool_duration_ms,
            )
            observations.append(error_observation)
            yield ToolCallFailed(observation=error_observation)
            continue

        # 工具是扩展边界，必须把未知执行器的普通异常转成 Observation；
        # CancelledError 继承 BaseException，不会在这里被吞掉。
        except Exception as error:  # noqa: BLE001
            # 普通执行异常同样记录等待时间
            tool_duration_ms = _elapsed_milliseconds(tool_started_at_ns)

            error_observation = ToolErrorObservation(
                tool_call_id=decision.tool_call_id,
                tool_name=decision.tool_name,
                code="tool_execution_failed",
                message="工具执行失败",
                details=type(error).__name__,
                duration_ms=tool_duration_ms,
            )
            observations.append(error_observation)
            yield ToolCallFailed(observation=error_observation)
            continue

        # 工具成功后计算本次执行耗时
        tool_duration_ms = _elapsed_milliseconds(tool_started_at_ns)

        success_observation = ToolObservation(
            tool_call_id=decision.tool_call_id,
            tool_name=decision.tool_name,
            result=tool_result,
            duration_ms=tool_duration_ms,
        )
        observations.append(success_observation)
        yield ToolCallSucceeded(observation=success_observation)

    yield AgentLoopCompleted(
        result=AgentLoopResult(
            status="max_steps_exceeded",
            answer=None,
            steps_taken=max_steps,
            observations=tuple(observations),
            model_usage=(total_model_usage if model_usage_is_complete else None),
            model_duration_ms=(
                total_model_duration_ms if model_duration_is_complete else None
            ),
            # 达到最大步数时也必须提供工具总耗时
            tool_duration_ms=_sum_tool_duration_ms(
                observations,
            ),
        )
    )


# 兼容原有调用方式。
async def run_agent_loop(
    decide: DecisionMaker,
    *,
    max_steps: int = 5,
    max_total_tokens: int | None = None,
) -> AgentLoopResult:
    """消费 Agent 事件流，并返回原有的最终结果。"""

    async for event in stream_agent_loop(
        decide,
        max_steps=max_steps,
        max_total_tokens=max_total_tokens,
    ):
        if isinstance(event, AgentLoopCompleted):
            return event.result

    # 按照事件协议，生成器必须产生 AgentLoopCompleted。
    # 这里防止未来修改生成器时遗漏终态。
    raise RuntimeError("Agent Loop 未产生终态事件")
