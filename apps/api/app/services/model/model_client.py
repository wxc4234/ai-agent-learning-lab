from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
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


@asynccontextmanager
async def chat_model_session(fallback_client, fallback_model):
    """一轮请求固定配置；新的配置只影响下一轮，退出时关闭自有客户端。"""
    from app.services.model.local_model_settings import override, validate_channel
    selected = override("chat")
    if selected is None:
        yield fallback_client, fallback_model, False
        return
    if not selected.enabled:
        raise ValueError("chat_model_disabled")
    validate_channel(selected, "chat")
    async with AsyncOpenAI(
        api_key=selected.api_key.get_secret_value(), base_url=selected.base_url,
        max_retries=0, timeout=60,
        http_client=DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False),
    ) as active:
        yield active, selected.model, True
