"""创建幂等事务：独立连接验证持久化，真实锁等待验证并发重放。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
from threading import Event

import pytest
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import Conversation, Task, TaskCreationRequest, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.tasks import task_service as service
from app.services.tasks.task_deletion_service import delete_workspace_task
from tests.tasks.test_task_deletion_service import wait_for_database_block
from tests.tasks.test_task_service import project as project  # noqa: PLC0414 -- 显式导出 pytest 夹具


KEY = '0123456789abcdef0123456789abcdef'


def create(session, project, *, key=KEY, title='学习 Agent'):
    return service.create_workspace_task(
        session, user_id=project[0], workspace_id=project[1],
        title=title, request_key=key,
    )


def counts(engine):
    # 使用独立连接确认已提交事实，不依赖被测 Session 的缓存。
    with Session(engine) as reader:
        return tuple(
            reader.scalar(select(func.count()).select_from(model))
            for model in (Task, Conversation, TaskCreationRequest)
        )


@pytest.mark.parametrize('expire', [True, False])
def test_replay_returns_committed_plain_result_without_writes(engine, project, expire):
    with Session(engine, expire_on_commit=expire) as session:
        first = create(session, project, title=' \t学习 Agent\n')
        assert not session.in_transaction()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with Session(engine, expire_on_commit=expire) as session:
            replay = create(session, project)
            assert not session.in_transaction()
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert replay == first
    assert set(asdict(replay)) == {
        'external_id', 'workspace_id', 'conversation_id', 'title', 'created_at',
    }
    assert not {'INSERT', 'UPDATE', 'DELETE'} & set(statements)
    assert counts(engine) == (1, 1, 1)
    with Session(engine) as reader:
        receipt = reader.scalar(select(TaskCreationRequest))
        assert receipt.request_hash == hashlib.sha256(
            '{"title":"学习 Agent"}'.encode()
        ).hexdigest()
        assert receipt.request_key == KEY
        assert receipt.user_id == project[0]
        assert reader.get(Task, receipt.task_id).external_id == first.external_id


@pytest.mark.parametrize('key', ['', 'a' * 31, 'a' * 33, 'A' * 32, 'g' * 32,
                                 ' ' + KEY, KEY + '\n', 123, [], {}, False])
def test_invalid_key_rolls_back_and_session_recovers(engine, project, key):
    with Session(engine) as session:
        with pytest.raises(service.InvalidTaskRequestKeyError):
            create(session, project, key=key)
        assert session.is_active and not session.in_transaction()
        assert counts(engine) == (0, 0, 0)
        create(session, project)
    assert counts(engine) == (1, 1, 1)


@pytest.mark.parametrize('key', [None, 'f' * 32])
def test_different_intent_creates_independent_task(engine, project, key):
    with Session(engine) as session:
        first = create(session, project, key=key)
        second = create(session, project)
    assert first.external_id != second.external_id
    assert first.conversation_id != second.conversation_id
    assert counts(engine) == (2, 2, 1 if key is None else 2)


def test_conflict_does_not_overwrite_original_and_session_recovers(engine, project):
    with Session(engine) as session:
        first = create(session, project)
        with pytest.raises(service.TaskCreationConflictError):
            create(session, project, title='另一份输入')
        assert session.is_active and not session.in_transaction()
        assert create(session, project) == first
    assert counts(engine) == (1, 1, 1)


def test_replay_uses_original_fingerprint_but_current_title(engine, project):
    with Session(engine) as session:
        first = create(session, project)
    with engine.begin() as connection:
        connection.execute(update(Task).values(title='自动总结后的标题'))
    with Session(engine) as session:
        replay = create(session, project)
        assert replay.external_id == first.external_id
        assert replay.title == '自动总结后的标题'
        with pytest.raises(service.TaskCreationConflictError):
            create(session, project, title=replay.title)
    assert counts(engine) == (1, 1, 1)


@pytest.mark.parametrize('other_owner', [False, True])
def test_key_scoped_by_project_and_owner(engine, project, other_owner):
    with Session(engine) as session, session.begin():
        owner = project[2] if other_owner else project[0]
        workspace = Workspace(external_id='b' * 32, user_id=owner, name='第二项目')
        session.add(workspace)
    with Session(engine) as session:
        first = create(session, project)
        second = create(session, (owner, 'b' * 32), title='不同输入')
    assert first.external_id != second.external_id
    assert counts(engine) == (2, 2, 2)


@pytest.mark.parametrize('key', [KEY, 'invalid'])
def test_authorization_precedes_receipt_lookup_and_validation(engine, project, key):
    with Session(engine) as session:
        create(session, project)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with Session(engine) as session:
            with pytest.raises(WorkspaceNotAccessibleError):
                create(session, (project[2], project[1]), key=key, title='')
            assert not session.in_transaction()
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not any('task_creation_requests' in sql for sql in statements)
    assert counts(engine) == (1, 1, 1)


@pytest.mark.parametrize('corruption', ['task-project', 'conversation-owner', 'missing-conversation'])
def test_replay_rechecks_current_resource_ownership(engine, project, corruption):
    with Session(engine) as session:
        create(session, project)
    with Session(engine) as session, session.begin():
        if corruption == 'task-project':
            workspace = Workspace(external_id='b' * 32, user_id=project[0], name='另一个项目')
            session.add(workspace)
            session.flush()
            session.execute(update(Task).values(workspace_id=workspace.id))
        elif corruption == 'conversation-owner':
            session.execute(update(Conversation).values(user_id=project[2]))
        else:
            session.execute(delete(Conversation))
    before = counts(engine)
    with Session(engine) as session:
        with pytest.raises(WorkspaceNotAccessibleError):
            create(session, project)
        assert not session.in_transaction()
    assert counts(engine) == before


def test_deleted_result_keeps_key_and_requires_new_intent(engine, project):
    with Session(engine) as session:
        first = create(session, project)
        delete_workspace_task(
            session, user_id=project[0], workspace_id=project[1], task_id=first.external_id,
        )
        with pytest.raises(service.TaskCreationResultDeletedError):
            create(session, project)
        assert not session.in_transaction()
        with pytest.raises(service.TaskCreationConflictError):
            create(session, project, title='不同输入')
        assert counts(engine) == (0, 0, 1)
        second = create(session, project, key='f' * 32)
    assert second.external_id != first.external_id
    with Session(engine) as reader:
        assert reader.scalar(select(TaskCreationRequest).where(
            TaskCreationRequest.request_key == KEY,
        )).task_id is None
    assert counts(engine) == (1, 1, 2)


@pytest.mark.parametrize('stage', ['receipt-insert', 'result', 'commit'])
def test_sql_failure_rolls_back_entire_creation(engine, project, monkeypatch, stage):
    def fail_receipt(mapper, connection, target):
        assert connection.scalar(select(func.count()).select_from(Task)) == 1
        assert connection.scalar(select(func.count()).select_from(Conversation)) == 1
        connection.execute(text('SELECT * FROM missing_idempotency_test_table'))

    with Session(engine) as session:
        def fail(*args, **kwargs):
            assert session.scalar(select(func.count()).select_from(TaskCreationRequest)) == 1
            session.execute(text('SELECT * FROM missing_idempotency_test_table'))

        with monkeypatch.context() as patch:
            if stage == 'receipt-insert':
                event.listen(TaskCreationRequest, 'before_insert', fail_receipt)
            elif stage == 'result':
                patch.setattr(service, 'TaskCreationResult', fail)
            else:
                patch.setattr(session, 'commit', fail)
            try:
                with pytest.raises(DBAPIError):
                    create(session, project)
            finally:
                if stage == 'receipt-insert':
                    event.remove(TaskCreationRequest, 'before_insert', fail_receipt)
        assert session.is_active and not session.in_transaction()
        assert counts(engine) == (0, 0, 0)
        create(session, project)
    assert counts(engine) == (1, 1, 1)


@pytest.mark.parametrize('operation,different', [('create', False), ('create', True), ('delete', False)])
@pytest.mark.parametrize('rollback', [False, True])
def test_real_project_lock_serializes_create_replay_and_delete(
    engine, project, operation, different, rollback,
):
    locked, release, waiting = Event(), Event(), Event()
    follower_pid = []
    original = None
    if operation == 'delete':
        with Session(engine) as session:
            original = create(session, project)

    def pause_before_commit(session):
        # 创建的三条记录或删除操作已 flush，尚未提交，项目锁仍持有。
        locked.set()
        assert release.wait(8)
        if rollback:
            raise RuntimeError('injected rollback')

    def leader():
        with Session(engine) as session:
            event.listen(session, 'before_commit', pause_before_commit)
            try:
                if operation == 'create':
                    return create(session, project)
                return delete_workspace_task(
                    session, user_id=project[0], workspace_id=project[1],
                    task_id=original.external_id,
                )
            except RuntimeError as exc:
                assert rollback and str(exc) == 'injected rollback'
                return None

    def capture(conn, cursor, statement, parameters, context, executemany):
        if locked.is_set() and statement.startswith('SELECT workspaces.') and 'FOR UPDATE' in statement:
            follower_pid.append(conn.scalar(text('SELECT pg_backend_pid()')))
            waiting.set()

    def follower():
        with Session(engine) as session:
            event.listen(session, 'after_begin', lambda s, tx, conn: conn.execute(
                text("SET LOCAL statement_timeout = '8s'"),
            ))
            try:
                result = create(session, project, title='不同输入' if different else '学习 Agent')
            except (service.TaskCreationConflictError, service.TaskCreationResultDeletedError) as exc:
                result = type(exc)
            assert not session.in_transaction()
            return result

    # 必须观察真实 pg_blocking_pids，再放行首个事务，避免仅靠线程启动顺序推断锁生效。
    with ThreadPoolExecutor(max_workers=2) as pool:
        event.listen(engine, 'before_cursor_execute', capture)
        try:
            first_future = pool.submit(leader)
            assert locked.wait(5)
            second_future = pool.submit(follower)
            assert waiting.wait(5)
            wait_for_database_block(engine, follower_pid[0])
        finally:
            release.set()
            event.remove(engine, 'before_cursor_execute', capture)
        first = first_future.result(timeout=10)
        second = second_future.result(timeout=10)

    if operation == 'delete':
        if rollback:
            assert second == original
        else:
            assert second is service.TaskCreationResultDeletedError
    elif rollback:
        assert second.title == ('不同输入' if different else '学习 Agent')
    elif different:
        assert second is service.TaskCreationConflictError
    else:
        assert second == first
    assert counts(engine) == ((0, 0, 1) if operation == 'delete' and not rollback else (1, 1, 1))


def test_commit_succeeded_but_confirmation_lost_can_replay(engine, project, monkeypatch):
    with Session(engine) as session:
        real_commit = session.commit

        def lose_confirmation():
            # 模拟服务端已完成 COMMIT，确认返回时异常；rollback 不能撤销已提交事实。
            real_commit()
            raise ConnectionError('commit confirmation lost')

        with monkeypatch.context() as patch:
            patch.setattr(session, 'commit', lose_confirmation)
            with pytest.raises(ConnectionError, match='confirmation lost'):
                create(session, project)
        assert not session.in_transaction()
        assert counts(engine) == (1, 1, 1)
        replay = create(session, project)
    with Session(engine) as reader:
        assert reader.scalar(select(Task)).external_id == replay.external_id
    assert counts(engine) == (1, 1, 1)


@pytest.mark.parametrize('deleted', [False, True])
def test_replay_refreshes_stale_identity_map(engine, project, deleted):
    with Session(engine) as writer:
        original = create(writer, project)
    with Session(engine, expire_on_commit=False) as session:
        # 强引用保留旧 ORM 对象，显式结束只读事务后再调用服务。
        receipt = session.scalar(select(TaskCreationRequest))
        task = session.scalar(select(Task))
        session.commit()
        with Session(engine) as writer:
            if deleted:
                delete_workspace_task(
                    writer, user_id=project[0], workspace_id=project[1],
                    task_id=original.external_id,
                )
            else:
                writer.execute(update(Task).values(title='新标题'))
                writer.commit()
        assert receipt.task_id is not None and task.title == '学习 Agent'
        if deleted:
            with pytest.raises(service.TaskCreationResultDeletedError):
                create(session, project)
        else:
            assert create(session, project).title == '新标题'
        assert not session.in_transaction()
    assert counts(engine) == ((0, 0, 1) if deleted else (1, 1, 1))
