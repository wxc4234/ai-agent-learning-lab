from collections.abc import AsyncIterator

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings

# API Key 只在连接模型时解封，其他模块不会接触敏感值。
client = AsyncOpenAI(
    api_key=settings.deepseek_api_key.get_secret_value(),
    base_url=settings.deepseek_base_url,
)


async def stream_chat_completion(
    messages: list[ChatCompletionMessageParam],
) -> AsyncIterator[str]:
    """将供应商的流式响应统一转换为纯文本片段。"""
    stream = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        stream=True,
    )

    async for chunk in stream:
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


"""唯一的模型客户端出口，集中处理供应商连接配置。"""
