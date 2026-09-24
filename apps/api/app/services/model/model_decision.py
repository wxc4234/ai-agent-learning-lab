"""把 DeepSeek 消息协议适配为通用 Agent Runtime 决策。"""

import json
from collections.abc import Sequence
from time import perf_counter_ns
from typing import ClassVar
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
)

from app.services.runtime.agent.agent_runtime import (
    AgentDecision,
    AgentObservation,
    FinalAnswer,
    ToolAction,
    ToolErrorObservation,
    ToolObservation,
    ModelUsage,
)
from app.tools.context import ToolExecutionContext
from app.tools.registry import ToolDefinition, model_tools_for_context

DEFAULT_SYSTEM_PROMPT = """你是一个可以使用工具解决问题的 AI 助手。
需要外部计算或实时信息时，请调用提供的工具；收到工具结果后再给出最终回答。
当前运行时每轮只支持调用一个工具，不要在同一条消息中请求多个工具。
用户要求继续时，先参考消息中已提供的同一会话历史，不要笼统声称无法访问历史。
历史可能被裁剪或不包含失败轮次；确实缺少问题时，明确指出缺少哪部分内容。
用户询问当前仓库的代码、文件或 session 实现时，优先使用已提供的文件查询工具核实。
只依据当前工具结果说明能力，不能声称能访问其他会话或未授权目录。
如果工具返回错误，请根据错误修正参数、改用其他方式，或向用户解释无法完成的原因。"""


class ModelDecisionError(RuntimeError):
    """内部诊断与公开错误分离，禁止把原始模型内容带到浏览器。"""

    MESSAGES: ClassVar[dict[str, str]] = {
        "multiple_tool_calls": "模型一次请求了多个工具，当前仅支持逐个执行。请重新发送原问题；本次未执行这批工具。",
        "empty_response": "模型未返回有效回答或工具调用。请重新发送原问题。",
        "missing_choice": "模型返回了空响应。请重新发送原问题。",
        "incomplete_response": "模型响应未完整结束，已显示内容可能不完整，未保存为完整回答或执行未完成的工具调用。请重试。",
        "unsupported_tool_type": "模型返回了不支持的工具类型，本次未执行该工具。",
        "history_mismatch": "本次工具结果上下文不一致，运行已停止。请重新发送原问题。",
        "invalid_response": "模型响应格式异常，运行已停止。请重新发送原问题。",
    }

    def __init__(self, message: str, *, reason: str = "invalid_response") -> None:
        super().__init__(message)
        # 只允许固定原因，不能把供应商返回值当作公开错误或日志字段。
        self.reason = reason if reason in self.MESSAGES else "invalid_response"
        self.public_message = self.MESSAGES[self.reason]



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
        tool_definitions: tuple[ToolDefinition, ...] | None = None,
    ) -> None:
        self._client = client
        self._model = model

        # 显式传入时，模型只看到本次执行能力快照中的描述。
        # 不序列化执行器、上下文、Run ID或恢复记录容器。
        self._tools = (
            model_tools_for_context(tool_context)
            if tool_definitions is None
            else [
                definition.as_model_tool()
                for definition in tool_definitions
            ]
        )

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
            # 与单工具 Runtime 一致；供应商仍可能违约，响应边界继续严格检查。
            parallel_tool_calls=False,
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
        return self._parse_response(response, model_duration_ms)

    def _parse_response(self, response: ChatCompletion, model_duration_ms: int) -> AgentDecision:
        """完整响应共用决策校验；流式参数未拼接完成前不得进入这里。"""
        model_usage = self._extract_model_usage(response)

        if not response.choices:
            raise ModelDecisionError("模型响应中没有可用的 choice", reason="missing_choice")

        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) in {"length", "content_filter"}:
            raise ModelDecisionError("模型响应未完整结束", reason="incomplete_response")
        message = choice.message
        tool_calls = message.tool_calls or []

        if len(tool_calls) > 1:
            # 不能只取第一个，也不能保存一条没有完整 tool 响应的
            # assistant 消息，否则下一次模型请求的上下文将不合法。
            raise ModelDecisionError(
                "当前 Agent Runtime 每轮只支持一个工具调用，"
                f"但模型一次返回了 {len(tool_calls)} 个",
                reason="multiple_tool_calls",
            )

        if tool_calls:
            tool_call = tool_calls[0]

            if tool_call.type != "function":
                raise ModelDecisionError(
                    "当前 Agent Runtime 不支持该工具类型",
                    reason="unsupported_tool_type",
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

        if not message.content or not message.content.strip():
            raise ModelDecisionError("模型既没有请求工具，也没有返回最终文本", reason="empty_response")

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
            raise ModelDecisionError("Agent Observation 历史发生倒退或改写", reason="history_mismatch")

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
