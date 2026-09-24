"""逐片段转发公开文本，完整聚合 Tool Calling 后才产生可执行决策。"""

from collections.abc import AsyncIterator
from time import perf_counter_ns

from openai.types.chat import ChatCompletion

from app.services.model.model_decision import DeepSeekDecisionMaker, ModelDecisionError
from app.services.runtime.agent.agent_runtime import AgentDecision, AgentObservation, ModelTextDelta


class StreamingDeepSeekDecisionMaker(DeepSeekDecisionMaker):
    async def stream_decisions(self, observations: tuple[AgentObservation, ...]) -> AsyncIterator[ModelTextDelta | AgentDecision]:
        self._append_new_observations(observations)
        started = perf_counter_ns()
        stream = await self._client.chat.completions.create(
            model=self._model, messages=self._messages, tools=self._tools,
            stream=True, stream_options={"include_usage": True}, parallel_tool_calls=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        texts: list[str] = []
        tool = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        has_tool = False
        finish = None
        usage = None
        size = 0
        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                if len(chunk.choices) != 1 or chunk.choices[0].index != 0 or finish is not None:
                    raise ModelDecisionError("模型流顺序异常")
                choice = chunk.choices[0]
                delta = choice.delta
                # reasoning_content 等供应商字段不转发、不存储。
                if delta.content:
                    size += len(delta.content.encode("utf-8"))
                    if size > 1024 * 1024:
                        raise ModelDecisionError("模型流超限", reason="incomplete_response")
                    first = not texts
                    texts.append(delta.content)
                    yield ModelTextDelta(delta.content, new_message=first)
                for part in delta.tool_calls or []:
                    if part.index != 0:
                        raise ModelDecisionError("模型流返回多个工具", reason="multiple_tool_calls")
                    if part.type is not None and part.type != "function":
                        raise ModelDecisionError("工具类型不支持", reason="unsupported_tool_type")
                    has_tool = True
                    identifier = part.id or ""
                    name = part.function.name or "" if part.function else ""
                    arguments = part.function.arguments or "" if part.function else ""
                    size += len((identifier + name + arguments).encode("utf-8"))
                    if size > 1024 * 1024:
                        raise ModelDecisionError("工具参数流超限", reason="incomplete_response")
                    tool["id"] += identifier
                    tool["function"]["name"] += name
                    tool["function"]["arguments"] += arguments
                if choice.finish_reason is not None:
                    finish = choice.finish_reason
            # EOF 本身不证明完成；截断的 JSON 绝不能成为工具调用。
            if finish != ("tool_calls" if has_tool else "stop"):
                raise ModelDecisionError("模型流缺少完整终态", reason="incomplete_response")
            if has_tool and (not tool["id"] or not tool["function"]["name"]):
                raise ModelDecisionError("工具标识不完整")
            response = ChatCompletion(
                id="assembled", object="chat.completion", created=0, model=self._model,
                usage=usage,
                choices=[{"index": 0, "finish_reason": finish, "message": {
                    "role": "assistant", "content": "".join(texts) or None,
                    "tool_calls": [tool] if has_tool else None,
                }}],
            )
            decision = self._parse_response(response, max(0, (perf_counter_ns() - started) // 1_000_000))
        finally:
            # 正常结束、解析失败及取消均关闭上游连接；不自动重试模型/工具。
            await stream.close()
        yield decision
