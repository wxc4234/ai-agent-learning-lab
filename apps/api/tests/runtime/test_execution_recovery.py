"""真实 PostgreSQL 恢复：停止证据、终态一致性、鉴权、提交失败。"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import AgentRun, AgentRunEvent, ConversationExecutionSlot, Message, TaskCreationRequest
from app.services.runtime import execution_recovery as recovery
from app.services.runtime.execution_process import host_identity, process_is_dead
from app.services.runtime.conversation_execution_service import acquire_conversation_execution
from app.services.tasks.task_service import create_workspace_task, TaskCreationResultDeletedError
from app.services.tasks.task_deletion_service import delete_workspace_task
from tests.tasks import test_task_deletion_service as deletion_tests

target = deletion_tests.target
assert_pair = deletion_tests.assert_pair
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError


@pytest.fixture(autouse=True)
def local(engine, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    monkeypatch.setattr(recovery, 'SessionLocal', sessionmaker(bind=engine))


def recover(target):
    recovery.recover_conversation_execution(user_id=target['user_id'], session_id=target['conversation_id'])


def seed(engine, target, host, pid):
    with Session(engine) as session, session.begin():
        session.add(ConversationExecutionSlot(conversation_id=target['conversation_pk'], owner_token='b' * 32, owner_host_id=host, owner_pid=pid))
        run = AgentRun(conversation_id=target['conversation_pk'], status='running', owner_host_id=host, owner_pid=pid)
        session.add(run)
        session.flush()
        return run.id


@pytest.mark.parametrize('kind', ['live', 'legacy', 'foreign', 'permission'])
def test_uncertain_owner_never_released(engine, target, monkeypatch, kind):
    host, pid = host_identity(), os.getpid()
    if kind == 'legacy':
        host, pid = None, None
    if kind == 'foreign':
        host = 'f' * 64
    if kind == 'permission':
        def denied(*args):
            raise PermissionError()
        monkeypatch.setattr(os, 'kill', denied)
    run_id = seed(engine, target, host, pid)
    with pytest.raises(recovery.ExecutionRecoveryRefusedError):
        recover(target)
    with Session(engine) as session:
        assert session.get(ConversationExecutionSlot, target['conversation_pk']) is not None
        assert session.get(AgentRun, run_id).status == 'running'
        assert session.scalar(select(AgentRunEvent.id)) is None


def test_real_process_killed_after_acquire_can_recover_and_execute_again(engine, target):
    # 子进程真实调用获取事务，提交后强制杀进程，不能用伪造的年龄证明退出。
    script = '''
import sys, json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.services.runtime.conversation_execution_service import acquire_conversation_execution
url, options = json.loads(sys.stdin.readline())
engine = create_engine(url, connect_args={'options': options})
with Session(engine) as session:
    acquire_conversation_execution(session, user_id=int(sys.argv[1]), session_id=sys.argv[2])
print('acquired', flush=True)
sys.stdin.read()
'''
    with engine.connect() as conn:
        schema = conn.scalar(text('SELECT current_schema()'))
    child = subprocess.Popen([sys.executable, '-c', script, str(target['user_id']), target['conversation_id']], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        child.stdin.write(json.dumps([engine.url.render_as_string(hide_password=False), f'-csearch_path={schema}']) + '\n')
        child.stdin.flush()
        assert child.stdout.readline().strip() == 'acquired'
        with Session(engine) as session, session.begin():
            session.add(AgentRun(conversation_id=target['conversation_pk'], status='running', owner_host_id=host_identity(), owner_pid=child.pid))
        with pytest.raises(recovery.ExecutionRecoveryRefusedError):
            recover(target)
        child.kill()
        child.wait(timeout=5)
        assert process_is_dead(host_identity(), child.pid)
        recover(target)
        recover(target)  # 重复请求无新终态，无重新运行。
        with Session(engine) as session:
            assert session.get(ConversationExecutionSlot, target['conversation_pk']) is None
            run = session.scalar(select(AgentRun))
            assert run.status == 'aborted' and run.finished_at
            assert session.scalars(select(AgentRunEvent.event_type)).all() == ['RUN_ABORTED', 'EXECUTION_RECOVERED']
            session.rollback()
            acquire_conversation_execution(session, user_id=target['user_id'], session_id=target['conversation_id'])
        with pytest.raises(recovery.ExecutionRecoveryRefusedError):
            recover(target)  # 新执行已取得占用，迟到恢复不影响它。
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()


def test_recovery_authorizes_before_process_probe(engine, target, monkeypatch):
    def forbidden(*args):
        pytest.fail('must authorize first')
    monkeypatch.setattr(recovery, 'process_is_dead', forbidden)
    with pytest.raises(ConversationNotAccessibleError):
        recovery.recover_conversation_execution(user_id=target['other_id'], session_id=target['conversation_id'])


def test_recovery_rollback_preserves_slot_and_run(engine, target, monkeypatch):
    run_id = seed(engine, target, host_identity(), os.getpid())
    monkeypatch.setattr(recovery, 'process_is_dead', lambda *args: True)
    from sqlalchemy import event
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('DELETE FROM conversation_execution_slots'):
            conn.execute(text('SELECT * FROM missing_recovery_table'))
    event.listen(engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(DBAPIError):
            recover(target)
    finally:
        event.remove(engine, 'before_cursor_execute', fail)
    with Session(engine) as session:
        assert session.get(ConversationExecutionSlot, target['conversation_pk'])
        assert session.get(AgentRun, run_id).status == 'running'
        assert session.scalar(select(AgentRunEvent.id)) is None


def test_delete_history_and_keep_creation_tombstone(engine, target):
    with Session(engine) as session:
        created = create_workspace_task(session, user_id=target['user_id'], workspace_id=target['workspace_id'], title='历史', request_key='1' * 32)
        from app.models import Conversation
        conversation = session.scalar(select(Conversation).where(Conversation.external_id == created.conversation_id))
        run = AgentRun(conversation_id=conversation.id, status='done', finished_at=datetime.now(timezone.utc))
        session.add_all([run, Message(conversation_id=conversation.id, role='user', content='历史')])
        session.flush()
        session.add(AgentRunEvent(run_id=run.id, event_type='RUN_FINISHED', payload={}))
        session.commit()
        delete_workspace_task(session, user_id=target['user_id'], workspace_id=target['workspace_id'], task_id=created.external_id)
        assert session.scalar(select(AgentRun.id)) is None
        assert session.scalar(select(Message.id)) is None
        assert session.scalar(select(AgentRunEvent.id)) is None
        assert session.scalar(select(TaskCreationRequest)).task_id is None
        session.rollback()
        with pytest.raises(TaskCreationResultDeletedError):
            create_workspace_task(session, user_id=target['user_id'], workspace_id=target['workspace_id'], title='历史', request_key='1' * 32)
    assert_pair(engine, target)


@pytest.mark.parametrize('stage', ['events', 'runs', 'messages', 'conversation', 'task', 'commit'])
def test_history_deletion_failure_rolls_back_all_records(engine, target, monkeypatch, stage):
    from sqlalchemy import event
    with Session(engine) as session, session.begin():
        run = AgentRun(conversation_id=target['conversation_pk'], status='done', finished_at=datetime.now(timezone.utc))
        session.add_all([run, Message(conversation_id=target['conversation_pk'], role='user', content='keep')])
        session.flush()
        run_id = run.id
        session.add(AgentRunEvent(run_id=run_id, event_type='RUN_FINISHED', payload={}))
    table = {'events': 'agent_run_events', 'runs': 'agent_runs', 'messages': 'messages', 'conversation': 'conversations', 'task': 'tasks'}.get(stage)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if table and statement.startswith(f'DELETE FROM {table} '):
            conn.execute(text('SELECT * FROM missing_history_delete_table'))
    event.listen(engine, 'before_cursor_execute', fail)
    try:
        with Session(engine) as session:
            if stage == 'commit':
                def fail_commit():
                    session.connection().execute(text('SELECT * FROM missing_history_commit_table'))
                monkeypatch.setattr(session, 'commit', fail_commit)
            with pytest.raises(DBAPIError):
                deletion_tests.remove(session, target)
            assert not session.in_transaction()
    finally:
        event.remove(engine, 'before_cursor_execute', fail)
    assert_pair(engine, target)
    with Session(engine) as session:
        assert session.get(AgentRun, run_id).status == 'done'
        assert session.scalar(select(AgentRunEvent.run_id)) == run_id
        assert session.scalar(select(Message.content)) == 'keep'


def test_run_without_slot_requires_its_own_dead_process_evidence(engine, target, monkeypatch):
    run_id = seed(engine, target, host_identity(), os.getpid())
    with Session(engine) as session, session.begin():
        session.delete(session.get(ConversationExecutionSlot, target['conversation_pk']))
    with pytest.raises(recovery.ExecutionRecoveryRefusedError):
        recover(target)
    monkeypatch.setattr(recovery, 'process_is_dead', lambda host, pid: host == host_identity() and pid == os.getpid())
    recover(target)
    with Session(engine) as session:
        assert session.get(AgentRun, run_id).status == 'aborted'
        assert session.get(ConversationExecutionSlot, target['conversation_pk']) is None


def test_dead_slot_does_not_authorize_unknown_run_owner(engine, target, monkeypatch):
    run_id = seed(engine, target, host_identity(), os.getpid())
    with Session(engine) as session, session.begin():
        session.get(AgentRun, run_id).owner_pid = None
    monkeypatch.setattr(recovery, 'process_is_dead', lambda host, pid: pid is not None)
    with pytest.raises(recovery.ExecutionRecoveryRefusedError):
        recover(target)
    with Session(engine) as session:
        assert session.get(ConversationExecutionSlot, target['conversation_pk'])
        assert session.get(AgentRun, run_id).status == 'running'
