"""工具集成测试的SDK流夹具：沿用完整响应的内容，提供真实chunk协议。"""

from types import SimpleNamespace

from openai.types.chat import ChatCompletionChunk

from tests.model import test_model_decision as complete


class CompletionStream:
    def __init__(self, response: SimpleNamespace):
        self.response = response
        self.closed = False

    async def __aiter__(self):
        message = self.response.choices[0].message
        calls = message.tool_calls
        # 本夹具用于授权/持久化集成；拆块、取消、超限另有流协议专项。
        yield ChatCompletionChunk.model_validate({
            'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0,
            'model': 'fixture', 'choices': [{
                'index': 0,
                'finish_reason': 'tool_calls' if calls else 'stop',
                'delta': {
                    'content': message.content,
                    'tool_calls': [
                        {'index': index, 'id': call.id, 'type': 'function',
                         'function': {'name': call.function.name, 'arguments': call.function.arguments}}
                        for index, call in enumerate(calls)
                    ] if calls else None,
                },
            }],
        })
        if self.response.usage is not None:
            yield ChatCompletionChunk.model_validate({
                'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0,
                'model': 'fixture', 'choices': [],
                'usage': self.response.usage.model_dump(),
            })

    async def close(self):
        self.closed = True


def build_text_response(content: str | None, *, usage: SimpleNamespace | None = None) -> CompletionStream:
    return CompletionStream(complete.build_text_response(content, usage=usage))


def build_tool_response(*tool_calls: tuple[str, str, str], usage: SimpleNamespace | None = None) -> CompletionStream:
    return CompletionStream(complete.build_tool_response(*tool_calls, usage=usage))
