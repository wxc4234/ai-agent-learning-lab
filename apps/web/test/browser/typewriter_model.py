"""隔离 UI 流式夹具：真实 SDK 适配器解析受控延迟 chunk。"""

import asyncio
from types import SimpleNamespace
from openai.types.chat import ChatCompletionChunk
from app.services.model.streaming_model_decision import StreamingDeepSeekDecisionMaker


async def stream_typewriter():
    text = '这是逐片段生成的 **Markdown 回复**。\n\n### 流式输出\n\n- 文字生成时立即出现\n- 完整结束后保存一次历史\n- 刷新后不会重复播放\n\n```python\nprint("你好，Agent")\n```\n\n本次验收只使用受控模型，不会修改项目文件。'

    class Stream:
        async def __aiter__(self):
            for offset in range(0, len(text), 3):
                await asyncio.sleep(0.3)
                yield ChatCompletionChunk.model_validate({
                    'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                    'choices': [{'index': 0, 'delta': {'content': text[offset:offset+3]}, 'finish_reason': None}],
                })
            yield ChatCompletionChunk.model_validate({
                'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 80, 'total_tokens': 100},
            })

        async def close(self):
            pass

    async def create(**kwargs):
        assert kwargs['stream'] is True
        return Stream()

    maker = StreamingDeepSeekDecisionMaker(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model='test', system_prompt='system', user_prompt='test',
    )
    async for part in maker.stream_decisions(()):
        yield part
