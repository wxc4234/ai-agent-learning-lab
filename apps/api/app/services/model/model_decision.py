"""把 DeepSeek 消息协议适配为通用 Agent Runtime 决策。"""

import json
from collections.abc import Sequence
from time import perf_counter_ns
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
)

from app.services.runtime.agent_runtime import (
    AgentDecision,
    AgentObservation,
    FinalAnswer,
    ToolAction,
    ToolErrorObservation,
    ToolObservation,
    ModelUsage,
)
from app.tools.context import ToolExecutionContext
from app.tools.registry import model_tools_for_context

DEFAULT_SYSTEM_PROMPT = """你是一个可以使用工具解决问题的 AI 助手。
需要外部计算或实时信息时，请调用提供的工具；收到工具结果后再给出最终回答。
当前运行时每轮只支持调用一个工具，不要在同一条消息中请求多个工具。
如果工具返回错误，请根据错误修正参数、改用其他方式，或向用户解释无法完成的原因。"""


class ModelDecisionError(RuntimeError):
    """模型返回了当前 Runtime 无法解释的响应。"""


class DeepSeekDecisionMaker:
    """维护一轮 Agent 运行的消息历史，并把模型消息转换为运行时决策。"""

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
        messages: Sequence[ChatCompletionMessageParam] | None = None,
        tool_context: ToolExecutionContext | None = None,
    ) -> None:
        self._client = client
        self._model = model

        # 上下文只用于服务端选择工具描述，不序列化进模型消息。
        # 本次执行保存独立列表，不修改全局 TOOLS。
        self._tools = model_tools_for_context(tool_context)

        if messages is not None:
            if system_prompt is not None or user_prompt is not None:
                raise ValueError("messages 不能和 system_prompt/user_prompt 同时传入")

            if not messages:
                raise ValueError("messages 不能为空")

            # 复制列表，避免适配器追加 assistant/tool 消息时，
            # 修改调用方持有的 messages_to_send。
            self._messages = list(messages)

        else:
            if system_prompt is None or user_prompt is None:
                raise ValueError(
                    "未提供 messages 时，必须同时提供 system_prompt 和 user_prompt"
                )

            self._messages: list[ChatCompletionMessageParam] = [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ]

        # Runtime 每轮传入累计 Observation。
        # 保存已处理前缀，可以避免重复追加，也能检测历史被改写。
        self._processed_observations: tuple[AgentObservation, ...] = ()

    async def __call__(
        self,
        observations: tuple[AgentObservation, ...],
    ) -> AgentDecision:
        """追加新的工具观察，调用模型，并返回一次工具动作或最终答案。"""
        self._append_new_observations(observations)

        # 只测量真实模型请求，不包含消息整理和后续解析。
        model_started_at_ns = perf_counter_ns()

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=self._messages,
            tools=self._tools,
            stream=False,
            extra_body={
                "thinking": {
                    "type": "disabled",
                }
            },
        )
        # 纳秒转换成整数毫秒；极快的 Mock 请求可以合法地得到 0。
        model_duration_ms = max(
            0,
            (perf_counter_ns() - model_started_at_ns) // 1_000_000,
        )
        model_usage = self._extract_model_usage(response)

        if not response.choices:
            raise ModelDecisionError("模型响应中没有可用的 choice")

        message = response.choices[0].message
        tool_calls = message.tool_calls or []

        if len(tool_calls) > 1:
            # 不能只取第一个，也不能保存一条没有完整 tool 响应的
            # assistant 消息，否则下一次模型请求的上下文将不合法。
            raise ModelDecisionError(
                "当前 Agent Runtime 每轮只支持一个工具调用，"
                f"但模型一次返回了 {len(tool_calls)} 个"
            )

        if tool_calls:
            tool_call = tool_calls[0]

            if tool_call.type != "function":
                raise ModelDecisionError(
                    f"当前 Agent Runtime 不支持工具类型：{tool_call.type}"
                )

            assistant_message: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                ],
            }

            # 必须保存 assistant 的原始 tool_calls。
            # 后续 tool message 要通过 tool_call_id 与它配对。
            self._messages.append(assistant_message)

            return ToolAction(
                tool_call_id=tool_call.id,
                tool_name=tool_call.function.name,
                arguments=tool_call.function.arguments,
                model_usage=model_usage,
                model_duration_ms=model_duration_ms,
            )

        if not message.content:
            raise ModelDecisionError("模型既没有请求工具，也没有返回最终文本")

        self._messages.append(
            {
                "role": "assistant",
                "content": message.content,
            }
        )

        return FinalAnswer(
            content=message.content,
            model_usage=model_usage,
            model_duration_ms=model_duration_ms,
        )

    @staticmethod
    def _extract_model_usage(response: ChatCompletion) -> ModelUsage | None:
        """把 DeepSeek/OpenAI 兼容 usage 转成 Runtime 通用结构。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None

        usage_payload = usage.model_dump()

        cache_hit_tokens = usage_payload.get("prompt_cache_hit_tokens")
        cache_miss_tokens = usage_payload.get("prompt_cache_miss_tokens")

        return ModelUsage(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cache_hit_input_tokens=(
                cache_hit_tokens if isinstance(cache_hit_tokens, int) else None
            ),
            cache_miss_input_tokens=(
                cache_miss_tokens if isinstance(cache_miss_tokens, int) else None
            ),
        )

    def _append_new_observations(
        self,
        observation: tuple[AgentObservation, ...],
    ) -> None:
        """只把 Runtime 新产生的 Observation 追加到模型消息历史。"""
        processed_count = len(self._processed_observations)

        if observation[:processed_count] != self._processed_observations:
            raise ModelDecisionError("Agent Observation 历史发生倒退或改写")

        new_observation = observation[processed_count:]

        self._messages.extend(
            self._serialize_observation(observation) for observation in new_observation
        )

        self._processed_observations = observation

    @staticmethod
    def _serialize_observation(
        observation: AgentObservation,
    ) -> ChatCompletionToolMessageParam:
        """把成功或失败的 Runtime Observation 转成 DeepSeek tool 消息。"""
        if isinstance(observation, ToolObservation):
            content = observation.result

        elif isinstance(observation, ToolErrorObservation):
            error: dict[str, str] = {
                "code": observation.code,
                "message": observation.message,
            }

            if observation.details is not None:
                error["details"] = observation.details

            content = json.dumps(
                {
                    "type": "tool_error",
                    "tool_name": observation.tool_name,
                    "error": error,
                },
                ensure_ascii=False,
            )

        else:
            raise ModelDecisionError(
                f"不支持的 Agent Observation：{type(observation).__name__}"
            )

        return {
            "role": "tool",
            "tool_call_id": observation.tool_call_id,
            "content": content,
        }
