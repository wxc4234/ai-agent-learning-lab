"""执行占用短事务：本地归属、真实锁竞争、精确释放和提交故障。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, ConversationExecutionSlot, User, Workspace
from app.repositories.chat.conversation_repository import ConversationNotAccessibleError
from app.services.runtime.execution import conversation_execution_service as service
from app.services.tasks.task_service import create_workspace_task
from tests.tasks.test_task_deletion_service import wait_for_database_block


@pytest.fixture
def target(engine, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    with Session(engine) as session, session.begin():
        owner = User(external_id='owner')
        other = User(external_id='other')
        workspace = Workspace(external_id='a' * 32, user=owner, name='项目')
        session.add_all([owner, other, workspace])
        session.flush()
        user_id, other_id = owner.id, other.id
    with Session(engine) as session:
        task = create_workspace_task(session, user_id=user_id, workspace_id='a' * 32, title='任务')
        second = create_workspace_task(session, user_id=user_id, workspace_id='a' * 32, title='第二任务')
    return user_id, task.conversation_id, other_id, second.conversation_id


def acquire(session, target):
    return service.acquire_conversation_execution(session, user_id=target[0], session_id=target[1])


def release(session, target, token):
    return service.release_conversation_execution(session, user_id=target[0], session_id=target[1], owner_token=token)


def tokens(engine):
    # 独立连接验证提交事实，不依赖被测 Session 的身份映射。
    with Session(engine) as reader:
        return list(reader.scalars(select(ConversationExecutionSlot.owner_token)).all())


@pytest.mark.parametrize('expire', [False, True])
def test_success_busy_release_reacquire_and_stale_release(engine, target, expire):
    with Session(engine, expire_on_commit=expire) as session:
        first = acquire(session, target)
        assert not session.in_transaction()
        assert tokens(engine) == [first.owner_token]
        assert set(asdict(first)) == {'session_id', 'owner_token', 'acquired_at'}
        assert first.session_id == target[1] and first.acquired_at.tzinfo is not None
        assert len(first.owner_token) == 32 and UUID(hex=first.owner_token).version == 4
        with pytest.raises(FrozenInstanceError):
            first.owner_token = 'f' * 32
        with pytest.raises(service.ConversationBusyError):
            acquire(session, target)
        assert session.is_active and not session.in_transaction()
        assert not release(session, target, 'f' * 32)
        assert tokens(engine) == [first.owner_token]
        assert release(session, target, first.owner_token)
        assert tokens(engine) == []
        assert not release(session, target, first.owner_token)
        second = acquire(session, target)
        assert first.owner_token != second.owner_token
        assert not release(session, target, first.owner_token)
        assert tokens(engine) == [second.owner_token]
        assert release(session, target, second.owner_token)
        assert not session.in_transaction()


@pytest.mark.parametrize('token', ['', 'a' * 31, 'a' * 33, 'A' * 32, 'g' * 32,
                                   'a' * 32 + '\n', None, 1, [], {}])
def test_invalid_token_preserves_slot_and_recovers_session(engine, target, token):
    with Session(engine) as session:
        owner = acquire(session, target)
        with pytest.raises(service.InvalidExecutionOwnerTokenError):
            release(session, target, token)
        assert session.is_active and not session.in_transaction()
        assert tokens(engine) == [owner.owner_token]
        assert release(session, target, owner.owner_token)


@pytest.mark.parametrize('operation', ['acquire', 'release'])
@pytest.mark.parametrize('kind', ['missing', 'other-user', 'standalone', 'workspace-owner', 'conversation-owner'])
def test_authorization_before_slot_access(engine, target, monkeypatch, operation, kind):
    with Session(engine) as session:
        owner = acquire(session, target)
    user_id, session_id = target[:2]
    with engine.begin() as conn:
        if kind == 'missing':
            session_id = 'f' * 32
        elif kind == 'other-user':
            user_id = target[2]
        elif kind == 'standalone':
            conn.execute(update(Conversation).where(Conversation.external_id == session_id).values(task_id=None))
        elif kind == 'workspace-owner':
            conn.execute(update(Workspace).values(user_id=target[2]))
        else:
            conn.execute(update(Conversation).where(Conversation.external_id == session_id).values(user_id=target[2]))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with Session(engine) as session:
            with pytest.raises(ConversationNotAccessibleError):
                if operation == 'acquire':
                    service.acquire_conversation_execution(session, user_id=user_id, session_id=session_id)
                else:
                    # 非法 token 也不能先于授权暴露不同分支。
                    service.release_conversation_execution(session, user_id=user_id, session_id=session_id, owner_token='bad')
            assert not session.in_transaction()
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not any('conversation_execution_slots' in sql for sql in statements)
    assert tokens(engine) == [owner.owner_token]


@pytest.mark.parametrize('operation', ['acquire', 'release'])
@pytest.mark.parametrize('kind', ['explicit', 'read', 'pending', 'flushed'])
def test_existing_transaction_is_not_touched(engine, target, operation, kind):
    with Session(engine) as session:
        if kind == 'explicit':
            session.begin()
        elif kind == 'read':
            session.execute(select(User.id))
        else:
            session.add(User(external_id='caller-work'))
            if kind == 'flushed':
                session.flush()
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match='无活动事务'):
            acquire(session, target) if operation == 'acquire' else release(session, target, 'a' * 32)
        assert session.get_transaction() is transaction and transaction.is_active
        session.commit()
    assert tokens(engine) == []


@pytest.mark.parametrize('operation', ['acquire', 'release'])
@pytest.mark.parametrize('stage', ['sql', 'before-commit', 'after-commit'])
def test_failure_and_commit_uncertainty(engine, target, monkeypatch, operation, stage):
    with Session(engine) as session:
        owner = acquire(session, target) if operation == 'release' else None
        real_execute = session.execute
        real_commit = session.commit
        # session.scalar 走 SQLAlchemy 内部执行，因此 SQL 故障在连接事件上注入。
        def fail_sql(conn, cursor, statement, parameters, context, executemany):
            if 'conversation_execution_slots' in statement:
                conn.exec_driver_sql('SELECT * FROM missing_execution_test_table')
        def fail_commit():
            if stage == 'after-commit':
                real_commit()
                raise ConnectionError('confirmation lost')
            real_execute(text('SELECT * FROM missing_execution_test_table'))
        with monkeypatch.context() as patch:
            if stage == 'sql':
                event.listen(engine, 'before_cursor_execute', fail_sql)
            else:
                patch.setattr(session, 'commit', fail_commit)
            try:
                with pytest.raises(ConnectionError if stage == 'after-commit' else DBAPIError):
                    acquire(session, target) if owner is None else release(session, target, owner.owner_token)
            finally:
                if stage == 'sql':
                    event.remove(engine, 'before_cursor_execute', fail_sql)
        assert session.is_active and not session.in_transaction()
        stored = tokens(engine)
        if operation == 'acquire':
            assert len(stored) == (1 if stage == 'after-commit' else 0)
            if stored:
                with pytest.raises(service.ConversationBusyError):
                    acquire(session, target)
            else:
                acquire(session, target)
        else:
            assert stored == ([] if stage == 'after-commit' else [owner.owner_token])
            assert release(session, target, owner.owner_token) is (stage != 'after-commit')


@pytest.mark.parametrize('operation', ['acquire', 'release'])
@pytest.mark.parametrize('rollback', [False, True])
def test_real_lock_wait_then_recheck_and_other_conversation_independence(engine, target, operation, rollback):
    ready, resume, waiting = Event(), Event(), Event()
    waiter_pid = []
    with Session(engine) as session:
        owner = acquire(session, target) if operation == 'release' else None
    def pause(session):
        ready.set()
        assert resume.wait(8)
        if rollback:
            raise RuntimeError('injected rollback')
    def leader():
        with Session(engine) as session:
            event.listen(session, 'before_commit', pause)
            try:
                return acquire(session, target) if owner is None else release(session, target, owner.owner_token)
            except RuntimeError as exc:
                assert rollback and str(exc) == 'injected rollback'
                return None
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('SELECT conversations.') and 'FOR UPDATE' in statement:
            waiter_pid.append(conn.scalar(text('SELECT pg_backend_pid()')))
            waiting.set()
    def follower():
        with Session(engine) as session:
            def after_begin(s, transaction, conn):
                conn.execute(text("SET LOCAL statement_timeout='8s'"))
                event.listen(conn, 'before_cursor_execute', capture)
            event.listen(session, 'after_begin', after_begin)
            try:
                return acquire(session, target)
            except service.ConversationBusyError:
                return 'busy'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(leader)
        try:
            assert ready.wait(5)
            second = pool.submit(follower)
            assert waiting.wait(5)
            wait_for_database_block(engine, waiter_pid[0])
            # 首会话仍被持锁时，另一个会话可获取并释放，不是全局串行锁。
            with Session(engine) as session:
                other = (target[0], target[3])
                unrelated = acquire(session, other)
                assert release(session, other, unrelated.owner_token)
        finally:
            resume.set()
        leader_result = first.result(timeout=10)
        follower_result = second.result(timeout=10)
    expect_busy = (operation == 'acquire' and not rollback) or (operation == 'release' and rollback)
    if expect_busy:
        assert follower_result == 'busy'
        assert tokens(engine) == [(owner if owner else leader_result).owner_token]
    else:
        assert tokens(engine) == [follower_result.owner_token]


def test_result_construction_failure_rolls_back_slot(engine, target, monkeypatch):
    with Session(engine) as session:
        def fail(**kwargs):
            assert session.scalar(select(ConversationExecutionSlot.owner_token)) == kwargs['owner_token']
            raise RuntimeError('result construction failed')
        with monkeypatch.context() as patch:
            patch.setattr(service, 'ConversationExecutionOwnership', fail)
            with pytest.raises(RuntimeError, match='result construction failed'):
                acquire(session, target)
        assert tokens(engine) == []
        assert not session.in_transaction()
        acquire(session, target)


def test_old_slot_and_terminal_run_do_not_authorize_takeover(engine, target):
    with Session(engine) as session:
        owner = acquire(session, target)
    with engine.begin() as conn:
        conn.execute(text("UPDATE conversation_execution_slots SET acquired_at=now()-interval '30 days'"))
        conn.execute(text("INSERT INTO agent_runs (conversation_id,status,finished_at) SELECT conversation_id,'aborted',now() FROM conversation_execution_slots"))
    with Session(engine) as session:
        with pytest.raises(service.ConversationBusyError):
            acquire(session, target)
        assert not session.in_transaction()
    assert tokens(engine) == [owner.owner_token]
