import asyncio

from openai import OpenAIError

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
    monkeypatch.setattr(chat_service, "record_run_event", lambda *args: None)
    monkeypatch.setattr(chat_service, "finish_agent_run", lambda *args: None)

    async def run_cancel():
        stream = chat_service.stream_chat_reply(
            session_id="cancel-test",
            prompt="生成一段长回答",
            run_id=101,
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


def test_completed_stream_records_chunks_and_finished_status(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "你好"},
    ]
    recorded_events: list[tuple[int, str, dict[str, object]]] = []
    finished_runs: list[tuple[int, str]] = []

    async def fake_prepare_messages(session_id, prompt):
        return history, []

    async def fake_stream_completion(messages):
        yield "你好，"
        yield "有什么可以帮你？"

    async def collect_stream():
        return [
            chunk
            async for chunk in chat_service.stream_chat_reply(
                session_id="done-test",
                prompt="你好",
                run_id=202,
            )
        ]

    monkeypatch.setattr(chat_service, "_prepare_chat_messages", fake_prepare_messages)
    monkeypatch.setattr(chat_service, "stream_chat_completion", fake_stream_completion)
    monkeypatch.setattr(
        chat_service,
        "record_run_event",
        lambda run_id, event_type, payload: recorded_events.append(
            (run_id, event_type, payload)
        ),
    )
    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status: finished_runs.append((run_id, status)),
    )
    monkeypatch.setattr(
        chat_service,
        "save_conversation_turn",
        lambda **kwargs: None,
    )

    chunks = asyncio.run(collect_stream())

    assert chunks == ["你好，", "有什么可以帮你？"]
    assert recorded_events == [
        (202, "TEXT_MESSAGE_CONTENT", {"chunk": "你好，"}),
        (202, "TEXT_MESSAGE_CONTENT", {"chunk": "有什么可以帮你？"}),
    ]
    assert finished_runs == [(202, "done")]
    assert history[-1] == {
        "role": "assistant",
        "content": "你好，有什么可以帮你？",
    }


def test_model_error_finishes_run_as_error_and_rolls_back_user_message(monkeypatch):
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "测试错误"},
    ]
    finished_runs: list[tuple[int, str]] = []

    async def fake_prepare_messages(session_id, prompt):
        return history, []

    async def fake_stream_completion(messages):
        raise OpenAIError("model unavailable")
        yield "不会到达"

    async def consume_stream():
        async for _chunk in chat_service.stream_chat_reply(
            session_id="error-test",
            prompt="测试错误",
            run_id=303,
        ):
            pass

    monkeypatch.setattr(chat_service, "_prepare_chat_messages", fake_prepare_messages)
    monkeypatch.setattr(chat_service, "stream_chat_completion", fake_stream_completion)
    monkeypatch.setattr(chat_service, "record_run_event", lambda *args: None)
    monkeypatch.setattr(
        chat_service,
        "finish_agent_run",
        lambda run_id, status: finished_runs.append((run_id, status)),
    )

    try:
        asyncio.run(consume_stream())
    except OpenAIError:
        pass
    else:
        raise AssertionError("模型异常应该继续向上传播")

    assert finished_runs == [(303, "error")]
