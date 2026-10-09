"""请求守卫在真实模型create前执行，支持普通与流式决策。"""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from openai import AsyncOpenAI
import pytest

from app.services.model.model_decision import DeepSeekDecisionMaker, ModelDecisionError
from app.services.model.streaming_model_decision import StreamingDeepSeekDecisionMaker
from tests.model.test_model_decision import build_text_response
from tests.model.response_stream import build_text_response as text_stream


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("outcome", ["pass", "reject", "cancel"])
def test_before_send_runs_before_create(streaming, outcome):
    events = []
    response = text_stream("answer") if streaming else build_text_response("answer")

    async def create(**kwargs):
        assert events == ["guard"]
        events.append("create")
        return response

    async def guard(messages, tools):
        assert [message["role"] for message in messages] == ["system", "user"]
        assert tools == []
        events.append("guard")
        if outcome == "reject":
            raise ModelDecisionError("PRIVATE", reason="code_context_send_rejected")
        if outcome == "cancel":
            raise asyncio.CancelledError()

    create_mock = AsyncMock(side_effect=create)
    client = cast(AsyncOpenAI, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))))
    cls = StreamingDeepSeekDecisionMaker if streaming else DeepSeekDecisionMaker
    maker = cls(client=client, model="fixture", system_prompt="system", user_prompt="query", tool_definitions=(), before_send=guard)

    async def run():
        if isinstance(maker, StreamingDeepSeekDecisionMaker):
            return [item async for item in maker.stream_decisions(())]
        return await maker(())

    if outcome == "pass":
        asyncio.run(run())
        assert create_mock.await_count == 1
    else:
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else ModelDecisionError):
            asyncio.run(run())
        assert create_mock.await_count == 0
