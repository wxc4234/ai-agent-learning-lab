import asyncio

from app.services import chat_service


def test_cancelled_stream_rolls_back_pending_user_message(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "生成一段长回答"},
    ]
    blocker = asyncio.Event()

    async def fake_prepare_messages(session_id, prompt):
        return history, []

    async def fake_stream_completion(messages):
        yield "第一段"
        await blocker.wait()

    monkeypatch.setattr(
        chat_service,
        "_prepare_chat_messages",
        fake_prepare_messages,
    )
    monkeypatch.setattr(
        chat_service,
        "stream_chat_completion",
        fake_stream_completion,
    )

    async def run_cancel():
        stream = chat_service.stream_chat_reply(
            session_id="cancel-test",
            prompt="生成一段长回答",
        )

        assert await anext(stream) == "第一段"

        async def read_next_chunk():
            return await anext(stream)

        next_chunk = asyncio.create_task(read_next_chunk())
        await asyncio.sleep(0)
        next_chunk.cancel()

        try:
            await next_chunk
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("取消信号没有继续向上传播")

    asyncio.run(run_cancel())

    assert history == [{"role": "system", "content": "system"}]
