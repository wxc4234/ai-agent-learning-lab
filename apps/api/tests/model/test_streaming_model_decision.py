"""真实 SDK chunk 结构的流协议测试，无模型费用或开发数据库访问。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletionChunk

from app.services.model.model_decision import ModelDecisionError
from app.services.model.streaming_model_decision import StreamingDeepSeekDecisionMaker
from app.services.runtime.agent.agent_runtime import FinalAnswer, ModelTextDelta, ToolAction, stream_agent_loop


def chunk(content=None, finish=None, tools=None, usage=None):
    return ChatCompletionChunk.model_validate({
        'id': 'test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
        'choices': [] if usage else [{'index': 0, 'finish_reason': finish,
            'delta': {'content': content, 'tool_calls': tools, 'reasoning_content': 'PRIVATE'}}],
        'usage': usage,
    })


class Stream:
    def __init__(self, parts):
        self.parts = parts
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            if isinstance(part, Exception):
                raise part
            yield part

    async def close(self):
        self.closed = True


def maker(parts):
    stream = Stream(parts)
    create = AsyncMock(return_value=stream)
    decide = StreamingDeepSeekDecisionMaker(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model='test', system_prompt='system', user_prompt='user',
    )
    return decide, stream, create


async def collect(decide):
    return [part async for part in decide.stream_decisions(())]


def test_text_arrives_before_upstream_finishes_and_usage_is_aggregated():
    decide, upstream, create = maker([chunk('你'), chunk('好'), chunk(finish='stop'), chunk(usage={
        'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12,
    })])

    async def run():
        stream = decide.stream_decisions(())
        first = await anext(stream)
        assert first == ModelTextDelta('你', True)
        assert not upstream.closed
        return [first, *[part async for part in stream]]

    parts = asyncio.run(run())
    assert parts[1] == ModelTextDelta('好')
    assert isinstance(parts[2], FinalAnswer) and parts[2].content == '你好'
    assert parts[2].model_usage.total_tokens == 12
    assert upstream.closed
    assert create.call_args.kwargs['stream'] is True
    assert create.call_args.kwargs['stream_options'] == {'include_usage': True}
    assert 'PRIVATE' not in str(parts) + str(decide._messages)


def test_fragmented_tool_arguments_only_emit_complete_action():
    decide, upstream, _ = maker([
        chunk(tools=[{'index': 0, 'id': 'call-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":'}}]),
        chunk(tools=[{'index': 0, 'function': {'arguments': '"a.py"}'}}]),
        chunk(finish='tool_calls'),
    ])
    parts = asyncio.run(collect(decide))
    assert len(parts) == 1 and isinstance(parts[0], ToolAction)
    assert parts[0].arguments == '{"path":"a.py"}'
    assert upstream.closed


@pytest.mark.parametrize('parts', [
    [chunk('partial')], [chunk('partial', finish='length')],
    [chunk('partial', finish='content_filter')], [chunk('  ', finish='stop')],
    [chunk(tools=[{'index': 1, 'id': 'two', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}])],
    [chunk('x' * (1024 * 1024 + 1))],
    [chunk(tools=[{'index': 0, 'id': 'call', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{'}}])],
    [chunk('a', finish='stop'), chunk('late')],
])
def test_invalid_or_incomplete_stream_never_produces_decision(parts):
    decide, upstream, _ = maker(parts)
    with pytest.raises(ModelDecisionError):
        asyncio.run(collect(decide))
    assert upstream.closed
    assert len(decide._messages) == 2


def test_network_failure_closes_stream():
    decide, upstream, _ = maker([chunk('partial'), OSError('offline')])
    with pytest.raises(OSError):
        asyncio.run(collect(decide))
    assert upstream.closed
    assert len(decide._messages) == 2


def test_runtime_close_releases_upstream_after_first_delta():
    decide, upstream, _ = maker([chunk('partial'), chunk('rest', finish='stop')])

    async def run():
        events = stream_agent_loop(decide)
        assert isinstance(await anext(events), ModelTextDelta)
        await events.aclose()
        assert upstream.closed

    asyncio.run(run())


def test_streamed_tool_round_feeds_complete_observation_before_final_text():
    from app.services.runtime.agent.agent_runtime import AgentLoopCompleted, ToolCallStarted
    first = Stream([
        chunk(tools=[{'index': 0, 'id': 'area', 'type': 'function', 'function': {'name': 'calculate_rectangle_area', 'arguments': '{"width":3,'}}]),
        chunk(tools=[{'index': 0, 'function': {'arguments': '"height":4}'}}]),
        chunk(finish='tool_calls'),
    ])
    second = Stream([chunk('面积是'), chunk('12。', finish='stop')])
    create = AsyncMock(side_effect=[first, second])
    decide = StreamingDeepSeekDecisionMaker(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model='test', system_prompt='system', user_prompt='area',
    )

    async def run():
        return [event async for event in stream_agent_loop(decide)]

    events = asyncio.run(run())
    assert len([event for event in events if isinstance(event, ToolCallStarted)]) == 1
    assert [event.content for event in events if isinstance(event, ModelTextDelta)] == ['面积是', '12。']
    assert isinstance(events[-1], AgentLoopCompleted)
    assert events[-1].result.answer == '面积是12。'
    messages = decide._messages
    assert messages[2]['tool_calls'][0]['function']['arguments'] == '{"width":3,"height":4}'
    assert messages[3]['role'] == 'tool' and messages[3]['tool_call_id'] == 'area'
    assert first.closed and second.closed
