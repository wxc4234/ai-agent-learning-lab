import asyncio
from collections.abc import AsyncIterator

from openai import OpenAIError
from openai.types.chat import ChatCompletionMessageParam

from app.config import settings
from app.repositories.conversation_repository import (
    load_conversation,
    save_conversation_turn,
)
from app.repositories.run_repository import finish_agent_run, record_run_event
from app.services.model_client import client, stream_chat_completion

# 内存缓存减少同一会话的重复数据库读取；服务重启后仍可由 PostgreSQL 恢复。
conversations: dict[str, list[ChatCompletionMessageParam]] = {}

# 只发送最近 5 轮，控制上下文长度、延迟和模型调用成本。
MAX_ROUNDS = 5


async def _prepare_chat_messages(
    session_id: str, prompt: str
) -> tuple[list[ChatCompletionMessageParam], list[ChatCompletionMessageParam]]:
    if session_id not in conversations:
        conversations[session_id] = [
            {
                "role": "system",
                "content": "你是一个贴心并且简洁的 AI 助手。",
            }
        ]

        saved_history = await asyncio.to_thread(
            load_conversation,
            session_id,
        )

        for message in saved_history:
            if message["role"] == "user":
                conversations[session_id].append(
                    {
                        "role": "user",
                        "content": message["content"],
                    }
                )
            elif message["role"] == "assistant":
                conversations[session_id].append(
                    {
                        "role": "assistant",
                        "content": message["content"],
                    }
                )

    history = conversations[session_id]
    history.append({"role": "user", "content": prompt})

    recent_history = history[1:-1][-(MAX_ROUNDS * 2) :]
    messages_to_send: list[ChatCompletionMessageParam] = [
        history[0],
        *recent_history,
        history[-1],
    ]
    return history, messages_to_send


async def create_chat_reply(session_id: str, prompt: str) -> str:
    """完成一轮聊天并返回纯文本回复，不包含任何 HTTP 细节。"""
    history, messages_to_send = await _prepare_chat_messages(
        session_id,
        prompt,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages_to_send,
            stream=False,
        )
        reply = response.choices[0].message.content or ""

        # 保存历史同样可能阻塞，因此不在 FastAPI 的异步事件循环中直接执行。
        await asyncio.to_thread(
            save_conversation_turn,
            session_id=session_id,
            user_content=prompt,
            assistant_content=reply,
        )

        history.append(
            {
                "role": "assistant",
                "content": reply,
            }
        )
        max_saved_messages = MAX_ROUNDS * 2
        if len(history) > max_saved_messages + 1:
            del history[1:-max_saved_messages]

        return reply
    except OpenAIError:
        # 模型失败时撤销刚追加的用户消息，避免内存会话留下不完整的一轮。
        history.pop()
        raise


async def stream_chat_reply(
    session_id: str,
    prompt: str,
    run_id: int,
) -> AsyncIterator[str]:
    """逐块返回模型输出，并记录完整运行事件。"""
    history = None
    chunks: list[str] = []

    try:
        history, message_to_send = await _prepare_chat_messages(
            session_id=session_id,
            prompt=prompt,
        )

        async for delta in stream_chat_completion(message_to_send):
            chunks.append(delta)

            await asyncio.to_thread(
                record_run_event,
                run_id,
                "TEXT_MESSAGE_CONTENT",
                {"chunk": delta},
            )

            yield delta

        reply = "".join(chunks)

        await asyncio.to_thread(
            save_conversation_turn,
            session_id=session_id,
            user_content=prompt,
            assistant_content=reply,
        )

        history.append({"role": "assistant", "content": reply})

        max_saved_message = MAX_ROUNDS * 2
        if len(history) > max_saved_message + 1:
            del history[1:-max_saved_message]

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            "done",
        )

    except asyncio.CancelledError:
        if history is not None:
            history.pop()

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            "aborted",
        )
        raise

    except OpenAIError:
        if history is not None:
            history.pop()

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            "error",
        )
        raise

    except Exception:
        if history is not None:
            history.pop()

        await asyncio.to_thread(
            finish_agent_run,
            run_id,
            "error",
        )
        raise


"""一轮聊天的业务编排：记忆恢复、模型调用和消息持久化。"""
