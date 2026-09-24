"""模型协议失败的安全诊断和同会话历史，不调用真实模型或开发数据库。"""

import asyncio
import json

import pytest

from app.services.chat import chat_service as service
from app.services.model.model_decision import ModelDecisionError
from app.services.runtime.execution.execution_threads import ExecutionThreads
from tests.chat.test_chat_stream import never_receive_cancellation, standalone_stream_owner  # noqa: F401


@pytest.mark.parametrize('reason', list(ModelDecisionError.MESSAGES))
def test_reason_reaches_stream_and_run_without_raw_response(monkeypatch, caplog, reason):
    history = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'question'}]
    finished = []

    async def prepare(**kwargs):
        return history, list(history)

    async def fail(*args, **kwargs):
        raise ModelDecisionError('PRIVATE raw response', reason=reason)
        yield

    monkeypatch.setattr(service.settings, 'app_mode', 'account')
    monkeypatch.setattr(service, '_prepare_chat_messages', prepare)
    monkeypatch.setattr(service, 'stream_agent_loop', fail)
    monkeypatch.setattr(service, 'wait_for_run_cancellation', never_receive_cancellation)
    monkeypatch.setattr(service, 'finish_agent_run', lambda *args: finished.append(args))

    async def collect():
        return [json.loads(line) async for line in service.stream_chat_reply(
            user_id=1, session_id='diagnostic', prompt='question', run_id=303,
        )]

    events = asyncio.run(collect())
    payload = {'code': 'invalid_model_decision', 'reason': reason,
               'message': ModelDecisionError.MESSAGES[reason]}
    assert events == [{'type': 'RUN_ERROR', **payload}]
    assert finished == [(303, 'error', payload)]
    assert history == [{'role': 'system', 'content': 'system'}]
    assert 'PRIVATE' not in str(events) + str(finished) + caplog.text
    assert f'run_id=303 reason={reason}' in caplog.text


def test_same_session_history_is_loaded_and_windowed_without_cross_session(monkeypatch):
    saved = [message for index in range(7) for message in (
        {'role': 'user', 'content': f'question-{index}'},
        {'role': 'assistant', 'content': f'answer-{index}'},
    )]
    authorized = []
    monkeypatch.setattr(service, 'conversations', {(2, 'same'): [{'role': 'user', 'content': 'PRIVATE'}]})
    monkeypatch.setattr(service, 'ensure_owned_conversation', lambda **kwargs: authorized.append(kwargs))
    monkeypatch.setattr(service, 'load_conversation', lambda **kwargs: saved)

    async def prepare():
        threads = ExecutionThreads()
        try:
            return await service._prepare_chat_messages(
                user_id=1, session_id='same', prompt='继续回答之前的问题', execution_threads=threads,
            )
        finally:
            await threads.wait_closed()

    _, sent = asyncio.run(prepare())
    assert authorized == [{'user_id': 1, 'session_id': 'same'}]
    assert sent[1:-1] == saved[-10:]
    assert sent[-1]['content'] == '继续回答之前的问题'
    assert 'PRIVATE' not in str(sent)
