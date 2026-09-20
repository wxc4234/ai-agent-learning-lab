"""任务删除：真实提交、回滚和 PostgreSQL 外键锁竞争。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import AgentRun, AgentRunEvent, Conversation, ConversationExecutionSlot, Message, Task, User, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.tasks import task_deletion_service as service


@pytest.fixture
def target(engine):
    with Session(engine) as session, session.begin():
        owner = User(external_id='owner')
        other = User(external_id='other')
        workspace = Workspace(external_id='a' * 32, name='项目', user=owner, root_path='/preserved')
        task = Task(external_id='b' * 32, title='空任务', workspace=workspace)
        conversation = Conversation(external_id='c' * 32, user=owner, task=task)
        sibling = Task(external_id='d' * 32, title='保留任务', workspace=workspace)
        sibling_conversation = Conversation(external_id='e' * 32, user=owner, task=sibling)
        session.add_all([owner, other, workspace, task, conversation, sibling, sibling_conversation])
        session.flush()
        return {
            'user_id': owner.id, 'other_id': other.id,
            'workspace_id': workspace.external_id, 'task_id': task.external_id,
            'conversation_id': conversation.external_id,
            'task_pk': task.id, 'conversation_pk': conversation.id,
        }


def remove(session, target, **overrides):
    args = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    args.update(overrides)
    return service.delete_workspace_task(session, **args)


def assert_pair(engine, target, exists=True):
    # 独立连接观察提交事实，不依赖删除服务中的 ORM 对象。
    with Session(engine) as reader:
        assert (reader.get(Task, target['task_pk']) is not None) is exists
        assert (reader.get(Conversation, target['conversation_pk']) is not None) is exists
        assert reader.scalar(select(Task).where(Task.external_id == 'd' * 32)) is not None
        assert reader.scalar(select(Conversation).where(Conversation.external_id == 'e' * 32)) is not None
        assert reader.scalar(select(Workspace.root_path)) == '/preserved'


def child(kind, target):
    if kind == 'slot':
        return ConversationExecutionSlot(conversation_id=target['conversation_pk'], owner_token='a' * 32)
    if kind == 'message':
        return Message(conversation_id=target['conversation_pk'], role='user', content='保留')
    return AgentRun(conversation_id=target['conversation_pk'], status='running')


@pytest.mark.parametrize('expire', [True, False])
def test_delete_commits_and_plain_result_survives_session_close(engine, target, expire):
    with Session(engine, expire_on_commit=expire) as session:
        result = remove(session, target)
        assert not session.in_transaction()
    assert asdict(result) == {key: target[key] for key in ('workspace_id', 'task_id', 'conversation_id')}
    with pytest.raises(FrozenInstanceError):
        result.task_id = 'changed'
    assert_pair(engine, target, False)
    with Session(engine) as session:
        with pytest.raises(WorkspaceNotAccessibleError):
            remove(session, target)
        assert not session.in_transaction()


@pytest.mark.parametrize('kind', ['missing-workspace', 'missing-task', 'foreign-owner', 'wrong-project', 'foreign-conversation', 'missing-conversation'])
def test_inaccessible_or_inconsistent_resources_do_not_delete(engine, target, kind):
    overrides = {}
    with Session(engine) as session, session.begin():
        if kind == 'missing-workspace':
            overrides['workspace_id'] = 'f' * 32
        elif kind == 'missing-task':
            overrides['task_id'] = 'f' * 32
        elif kind == 'foreign-owner':
            overrides['user_id'] = target['other_id']
        elif kind == 'wrong-project':
            session.add(Workspace(external_id='f' * 32, name='其他项目', user_id=target['user_id']))
            overrides['workspace_id'] = 'f' * 32
        elif kind == 'foreign-conversation':
            session.get(Conversation, target['conversation_pk']).user_id = target['other_id']
        else:
            session.delete(session.get(Conversation, target['conversation_pk']))
    with Session(engine) as session:
        with pytest.raises(WorkspaceNotAccessibleError):
            remove(session, target, **overrides)
        assert not session.in_transaction()
    with Session(engine) as reader:
        assert reader.get(Task, target['task_pk']) is not None
        assert (reader.get(Conversation, target['conversation_pk']) is None) is (kind == 'missing-conversation')


@pytest.mark.parametrize('kind', ['running', 'done', 'aborted', 'error', 'unknown'])
def test_any_message_or_run_blocks_deletion_and_preserves_events(engine, target, kind):
    with Session(engine) as session, session.begin():
        row = child('message' if kind == 'message' else 'run', target)
        if isinstance(row, AgentRun):
            row.status = kind
            session.add(row)
            session.flush()
            session.add(AgentRunEvent(run_id=row.id, event_type='PRESERVE', payload={'keep': True}))
        else:
            session.add(row)
    with Session(engine) as session:
        with pytest.raises(service.TaskRunUnsettledError):
            remove(session, target)
        assert session.is_active and not session.in_transaction()
    assert_pair(engine, target)
    with Session(engine) as reader:
        if kind == 'message':
            assert reader.scalar(select(Message.content)) == '保留'
        else:
            assert reader.scalar(select(AgentRun.status)) == kind
            assert reader.scalar(select(AgentRunEvent.payload)) == {'keep': True}


@pytest.mark.parametrize('kind', ['explicit', 'read', 'pending', 'flushed'])
def test_existing_transaction_is_not_rolled_back(engine, target, kind):
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
            remove(session, target)
        assert session.get_transaction() is transaction and transaction.is_active
        session.commit()
    assert_pair(engine, target)
    if kind in ('pending', 'flushed'):
        with Session(engine) as reader:
            assert reader.scalar(select(User.id).where(User.external_id == 'caller-work')) is not None


@pytest.mark.parametrize('stage', ['result', 'second-delete', 'commit'])
def test_failure_rolls_back_entire_deletion_and_session_is_reusable(engine, target, monkeypatch, stage):
    with Session(engine) as session:
        def fail(*args, **kwargs):
            # 在同一事务内制造真实 SQL 错误；第二次 DELETE 前会话已不存在。
            if stage == 'second-delete':
                assert session.connection().scalar(select(Conversation.id).where(Conversation.id == target['conversation_pk'])) is None
            session.connection().execute(text('SELECT * FROM missing_deletion_test_table'))
        original_execute = session.execute
        def execute(statement, *args, **kwargs):
            if getattr(statement, 'is_delete', False) and statement.table.name == 'tasks':
                fail()
            return original_execute(statement, *args, **kwargs)
        with monkeypatch.context() as patch:
            if stage == 'result':
                patch.setattr(service, 'TaskDeletionResult', fail)
            elif stage == 'commit':
                patch.setattr(session, 'commit', fail)
            else:
                patch.setattr(session, 'execute', execute)
            with pytest.raises(DBAPIError):
                remove(session, target)
        assert session.is_active and not session.in_transaction()
        assert_pair(engine, target)
        remove(session, target)
    assert_pair(engine, target, False)


def wait_for_database_block(engine, pid):
    # 观察 PostgreSQL 实际锁等待，而不是用固定 sleep 猜测并发顺序。
    deadline = monotonic() + 5
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
        while monotonic() < deadline:
            if observer.scalar(text('SELECT cardinality(pg_blocking_pids(:pid))'), {'pid': pid}):
                return
            sleep(0.01)
    pytest.fail('expected PostgreSQL lock wait was not observed')


@pytest.mark.parametrize('kind', ['message', 'run', 'slot'])
@pytest.mark.parametrize('rollback', [False, True])
def test_locked_deletion_blocks_child_insert_until_commit_or_rollback(engine, target, kind, rollback):
    locked, release, inserting = Event(), Event(), Event()
    writer_pid = []
    def pause_after_lock(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('SELECT agent_runs.id'):
            locked.set()
            assert release.wait(8)
            if rollback:
                raise RuntimeError('injected rollback')
    def deletion():
        with Session(engine) as session:
            event.listen(session.bind, 'before_cursor_execute', pause_after_lock)
            try:
                remove(session, target)
                return 'committed'
            except RuntimeError:
                if not rollback:
                    raise
                return 'rolled-back'
            finally:
                event.remove(session.bind, 'before_cursor_execute', pause_after_lock)
    def insertion():
        with Session(engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '8s'"))
            writer_pid.append(session.scalar(text('SELECT pg_backend_pid()')))
            inserting.set()
            session.add(child(kind, target))
            try:
                session.commit()
                return 'committed'
            except DBAPIError as exc:
                session.rollback()
                return exc.orig.sqlstate
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(deletion)
        try:
            assert locked.wait(5)
            writing = pool.submit(insertion)
            assert inserting.wait(5)
            wait_for_database_block(engine, writer_pid[0])
        finally:
            release.set()
        assert deleting.result(timeout=10) == ('rolled-back' if rollback else 'committed')
        # 同一次等待中的 INSERT：删除提交后外键失败，删除回滚后成功。
        assert writing.result(timeout=10) == ('committed' if rollback else '23503')
    assert_pair(engine, target, rollback)


@pytest.mark.parametrize('kind', ['message', 'run', 'slot'])
def test_child_insert_wins_then_deletion_rechecks_history(engine, target, kind):
    waiting = Event()
    deletion_pid = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('SELECT conversations.') and 'FOR UPDATE' in statement:
            deletion_pid.append(connection.scalar(text('SELECT pg_backend_pid()')))
            waiting.set()
    def deletion():
        with Session(engine) as session:
            event.listen(session, 'after_begin', lambda s, tx, conn: conn.execute(text("SET LOCAL statement_timeout = '8s'")))
            if kind == 'message':
                remove(session, target)
            else:
                with pytest.raises(service.ConversationBusyError if kind == 'slot' else service.TaskRunUnsettledError):
                    remove(session, target)
            assert not session.in_transaction()
    with Session(engine) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.add(child(kind, target))
        writer.flush()
        event.listen(engine, 'before_cursor_execute', capture)
        try:
            deleting = pool.submit(deletion)
            assert waiting.wait(5)
            wait_for_database_block(engine, deletion_pid[0])
        finally:
            # 提交先发生的子记录，让等待会话锁的删除事务继续并重新检查。
            writer.commit()
            event.remove(engine, 'before_cursor_execute', capture)
        deleting.result(timeout=10)
    assert_pair(engine, target, kind != 'message')


@pytest.mark.parametrize('status', [None, 'running', 'done', 'error', 'aborted'])
def test_slot_blocks_empty_or_terminal_task_without_reading_token(engine, target, status):
    # 占用早于 Run 创建，或终态已写入但执行仍在收尾，都必须拒绝。
    with Session(engine) as writer, writer.begin():
        writer.add(child('slot', target))
        if status is not None:
            writer.add(AgentRun(conversation_id=target['conversation_pk'], status=status))
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with Session(engine) as session:
            with pytest.raises(service.ConversationBusyError) as caught:
                remove(session, target)
            assert caught.value.code == 'conversation_busy'
            assert session.is_active and not session.in_transaction()
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not any('owner_token' in sql or sql.startswith('DELETE') for sql in statements)
    assert_pair(engine, target)
    with Session(engine) as reader:
        assert reader.get(ConversationExecutionSlot, target['conversation_pk']).owner_token == 'a' * 32
        assert reader.scalar(select(AgentRun.status)) == status


def test_real_release_allows_empty_task_deletion_in_local_mode(engine, target, monkeypatch):
    from app.config import settings
    from app.services.runtime.execution.conversation_execution_service import (
        acquire_conversation_execution,
        release_conversation_execution,
    )
    monkeypatch.setattr(settings, 'app_mode', 'local')
    with Session(engine) as session:
        ownership = acquire_conversation_execution(
            session, user_id=target['user_id'], session_id=target['conversation_id'],
        )
        with pytest.raises(service.ConversationBusyError):
            remove(session, target)
        assert release_conversation_execution(
            session, user_id=target['user_id'], session_id=target['conversation_id'],
            owner_token=ownership.owner_token,
        )
        remove(session, target)
    assert_pair(engine, target, False)


def test_unauthorized_deletion_does_not_query_slot(engine, target):
    with Session(engine) as writer, writer.begin():
        writer.add(child('slot', target))
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with Session(engine) as session:
            with pytest.raises(WorkspaceNotAccessibleError):
                remove(session, target, user_id=target['other_id'])
            assert not session.in_transaction()
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not any('conversation_execution_slots' in sql for sql in statements)
    assert_pair(engine, target)


def test_slot_query_database_failure_rolls_back_and_does_not_mean_idle(engine, target):
    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('SELECT conversation_execution_slots.conversation_id'):
            connection.execute(text('SELECT * FROM missing_slot_query_table'))
    event.listen(engine, 'before_cursor_execute', fail)
    try:
        with Session(engine) as session:
            with pytest.raises(DBAPIError):
                remove(session, target)
            assert session.is_active and not session.in_transaction()
    finally:
        event.remove(engine, 'before_cursor_execute', fail)
    assert_pair(engine, target)
