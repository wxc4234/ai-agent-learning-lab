"""恢复 HTTP 的授权、明确拒绝与事务结果。"""

import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Conversation, ConversationExecutionSlot
from app.services.runtime.execution import execution_recovery as recovery
from app.services.runtime.execution.execution_process import host_identity
from tests.chat import test_conversation_execution_api as query_tests
from tests.local.test_local_mode import HEADERS

local_client = query_tests.local_client
lab = query_tests.lab


@pytest.mark.parametrize('kind,status', [('live', 409), ('legacy', 409), ('dead', 204), ('missing', 204)])
def test_recovery_http_contract(lab, engine, monkeypatch, kind, status):
    client, _, session_id, _ = lab
    monkeypatch.setattr(recovery, 'SessionLocal', sessionmaker(bind=engine))
    with Session(engine) as session, session.begin():
        conversation_id = session.scalar(select(Conversation.id).where(Conversation.external_id == session_id))
        if kind != 'missing':
            session.add(ConversationExecutionSlot(conversation_id=conversation_id, owner_token='b' * 32,
                owner_host_id=host_identity() if kind != 'legacy' else None, owner_pid=os.getpid()))
    if kind == 'dead':
        monkeypatch.setattr(recovery, 'process_is_dead', lambda *args: True)
    response = client.post(f'/sessions/{session_id}/execution/recover', headers=HEADERS, json={})
    assert response.status_code == status
    assert response.headers['cache-control'] == 'no-store'
    assert 'owner_token' not in response.text and 'owner_pid' not in response.text
    if status == 204:
        assert response.content == b''
    else:
        assert response.json()['code'] == 'execution_recovery_refused'
    with Session(engine) as session:
        assert (session.get(ConversationExecutionSlot, conversation_id) is not None) == (status == 409)


@pytest.mark.parametrize('body', [{'force': True}, {'user_id': 1}, {'owner_pid': 1}, []])
def test_recovery_rejects_extra_input(lab, body):
    client, _, session_id, _ = lab
    response = client.post(f'/sessions/{session_id}/execution/recover', headers=HEADERS, json=body)
    assert response.status_code == 422


def test_missing_origin_and_foreign_conversation(lab, engine, monkeypatch):
    client, _, session_id, other_id = lab
    monkeypatch.setattr(recovery, 'SessionLocal', sessionmaker(bind=engine))
    assert client.post(f'/sessions/{session_id}/execution/recover', headers={'X-Local-Runtime-Token': HEADERS['X-Local-Runtime-Token']}, json={}).status_code == 403
    with Session(engine) as session, session.begin():
        session.scalar(select(Conversation).where(Conversation.external_id == session_id)).user_id = other_id
    response = client.post(f'/sessions/{session_id}/execution/recover', headers=HEADERS, json={})
    assert response.status_code == 404
