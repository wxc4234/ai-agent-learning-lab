"""公开文本增量、完整落库及取消/失败边界。"""

import asyncio
import json

import pytest

from app.services.chat import chat_service as service
from app.services.model.model_decision import ModelDecisionError
from app.services.runtime.agent.agent_runtime import ModelTextDelta, FinalAnswer, ModelUsage
from tests.chat.test_chat_stream import standalone_stream_owner, never_receive_cancellation  # noqa: F401


@pytest.mark.parametrize('outcome', ['done', 'error', 'cancel'])
def test_partial_text_is_live_but_only_complete_turn_is_saved(monkeypatch, outcome):
    history = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'q'}]
    saved, recorded, finished = [], [], []
    closed = []

    class Decision:
        def __init__(self, **kwargs):
            pass

        async def stream_decisions(self, observations):
            try:
                yield ModelTextDelta('**你', True)
                if outcome == 'error':
                    raise ModelDecisionError('incomplete', reason='incomplete_response')
                if outcome == 'cancel':
                    await asyncio.Future()
                yield ModelTextDelta('好**')
                yield FinalAnswer('**你好**', ModelUsage(2, 2, 4), 10)
            finally:
                closed.append(True)

    async def prepare(**kwargs):
        return history, list(history)

    monkeypatch.setattr(service.settings, 'app_mode', 'account')
    monkeypatch.setattr(service, 'DeepSeekDecisionMaker', Decision)
    monkeypatch.setattr(service, '_prepare_chat_messages', prepare)
    monkeypatch.setattr(service, 'wait_for_run_cancellation', never_receive_cancellation)
    monkeypatch.setattr(service, 'get_run_cancellation_reason', lambda _: 'user')
    monkeypatch.setattr(service, 'save_conversation_turn', lambda **kwargs: saved.append(kwargs))
    monkeypatch.setattr(service, 'record_run_event', lambda *args: recorded.append(args))
    monkeypatch.setattr(service, 'finish_agent_run', lambda *args: finished.append(args))

    async def run():
        stream = service.stream_chat_reply(user_id=1, session_id='s', prompt='q', run_id=1)
        assert json.loads(await anext(stream))['type'] == 'TEXT_MESSAGE_START'
        first = json.loads(await anext(stream))
        assert first['chunk'] == '**你' and not saved and not recorded
        if outcome == 'cancel':
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            return []
        return [json.loads(line) async for line in stream]

    events = asyncio.run(run())
    assert closed == [True]
    if outcome == 'done':
        assert [event['chunk'] for event in events if event['type'] == 'TEXT_MESSAGE_CONTENT'] == ['好**']
        assert events[-1]['type'] == 'RUN_FINISHED'
        assert len(saved) == 1 and saved[0]['assistant_content'] == '**你好**'
        assert len([event for event in recorded if event[1] == 'TEXT_MESSAGE_CONTENT']) == 1
    else:
        assert not saved
        assert len(history) == 1
        if outcome == 'error':
            assert events[-1]['type'] == 'RUN_ERROR'
    assert finished[-1][1] == {'done': 'done', 'error': 'error', 'cancel': 'aborted'}[outcome]
