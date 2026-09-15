"""Task 创建事务边界，使用隔离 PostgreSQL 和独立 Session 验证提交事实。"""

from dataclasses import FrozenInstanceError, asdict
from uuid import UUID

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.models import Conversation, Task, User, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.tasks import task_service as service


@pytest.fixture
def project(engine):
    with Session(engine) as session, session.begin():
        owner = User(external_id='task-owner')
        other = User(external_id='other-owner')
        workspace = Workspace(external_id='a' * 32, name='项目', user=owner)
        session.add_all([owner, other, workspace])
        session.flush()
        return owner.id, workspace.external_id, other.id


def create(session, project, title='任务'):
    return service.create_workspace_task(session, user_id=project[0], workspace_id=project[1], title=title)


def counts(engine):
    # 独立连接验证数据库事实，不能只检查同一 Session 的身份映射。
    with Session(engine) as reader:
        return tuple(reader.scalar(select(func.count()).select_from(model)) for model in (Task, Conversation))


@pytest.mark.parametrize('expire', [True, False])
def test_success_commits_pair_and_returns_plain_data(engine, project, expire):
    with Session(engine, expire_on_commit=expire) as session:
        result = create(session, project, ' \t新任务\n')
        assert not session.in_transaction()
        with Session(engine) as reader:
            task = reader.scalar(select(Task))
            conversation = reader.scalar(select(Conversation))
            assert task.workspace.external_id == result.workspace_id == project[1]
            assert task.workspace.root_path is None
            assert task.title == conversation.title == result.title == '新任务'
            assert conversation.user_id == task.workspace.user_id == project[0]
            assert task.conversation is conversation
            assert task.external_id == result.external_id
            assert conversation.external_id == result.conversation_id
            assert task.created_at == result.created_at
    assert set(asdict(result)) == {'external_id', 'workspace_id', 'conversation_id', 'title', 'created_at'}
    assert result.created_at.tzinfo is not None
    assert result.external_id != result.conversation_id
    assert UUID(hex=result.external_id).version == UUID(hex=result.conversation_id).version == 4
    with pytest.raises(FrozenInstanceError):
        result.title = '不允许修改'


@pytest.mark.parametrize('title', ['', ' \t\n\u3000', '字' * 201, '😀' * 201, None, 123, [], {}])
def test_invalid_title_rolls_back_and_session_recovers(engine, project, title):
    with Session(engine) as session:
        with pytest.raises(service.InvalidTaskTitleError):
            create(session, project, title)
        assert session.is_active and not session.in_transaction()
        assert counts(engine) == (0, 0)
        create(session, project)
    assert counts(engine) == (1, 1)


@pytest.mark.parametrize('title', ['字', '😀' * 200])
def test_boundary_titles_and_same_name_are_allowed(engine, project, title):
    with Session(engine) as session:
        first = create(session, project, title)
        second = create(session, project, title)
        assert first.external_id != second.external_id
        assert first.conversation_id != second.conversation_id
    assert counts(engine) == (2, 2)


@pytest.mark.parametrize('kind', ['missing', 'other-owner', 'unknown-user'])
@pytest.mark.parametrize('title', ['正常', ''])
def test_ownership_rejected_before_title_and_writes(engine, project, kind, title):
    user_id = project[0] if kind == 'missing' else project[2] if kind == 'other-owner' else 999999
    workspace_id = 'f' * 32 if kind == 'missing' else project[1]
    with Session(engine) as session:
        with pytest.raises(WorkspaceNotAccessibleError):
            service.create_workspace_task(session, user_id=user_id, workspace_id=workspace_id, title=title)
        assert not session.in_transaction()
    assert counts(engine) == (0, 0)


@pytest.mark.parametrize('kind', ['explicit', 'read', 'pending', 'flushed'])
def test_existing_transaction_untouched(engine, project, kind):
    with Session(engine) as session:
        pending = None
        if kind == 'explicit':
            session.begin()
        elif kind == 'read':
            session.execute(select(User.id))
        else:
            pending = User(external_id='caller-work')
            session.add(pending)
            if kind == 'flushed':
                session.flush()
        transaction = session.get_transaction()
        with pytest.raises(RuntimeError, match='无活动事务'):
            create(session, project)
        assert session.get_transaction() is transaction and transaction.is_active
        if kind == 'pending':
            assert pending in session.new and pending.id is None
        session.commit()
    assert counts(engine) == (0, 0)
    if pending is not None:
        with Session(engine) as reader:
            assert reader.scalar(select(User).where(User.external_id == 'caller-work')) is not None


def test_conversation_sql_failure_rolls_back_already_inserted_task(engine, project):
    def fail_conversation(mapper, connection, target):
        # 确保 Task 的 INSERT 已真实执行，再让 Conversation 写入阶段发生数据库错误。
        assert connection.scalar(select(func.count()).select_from(Task)) == 1
        connection.execute(text('SELECT * FROM missing_task_service_table'))
    with Session(engine) as session:
        event.listen(Conversation, 'before_insert', fail_conversation)
        try:
            with pytest.raises(DBAPIError):
                create(session, project)
        finally:
            event.remove(Conversation, 'before_insert', fail_conversation)
        assert session.is_active and not session.in_transaction()
        assert counts(engine) == (0, 0)
        create(session, project)
    assert counts(engine) == (1, 1)


@pytest.mark.parametrize('stage', ['result', 'commit'])
def test_post_flush_failure_rolls_back_both_records(engine, project, monkeypatch, stage):
    with Session(engine) as session:
        failure = OperationalError('COMMIT', None, RuntimeError('synthetic failure'))
        def fail(*args, **kwargs):
            assert session.scalar(select(func.count()).select_from(Task)) == 1
            assert session.scalar(select(func.count()).select_from(Conversation)) == 1
            raise failure
        with monkeypatch.context() as patch:
            if stage == 'commit':
                patch.setattr(session, 'commit', fail)
            else:
                patch.setattr(service, 'TaskCreationResult', fail)
            with pytest.raises(OperationalError) as caught:
                create(session, project)
            assert caught.value is failure
        assert session.is_active and not session.in_transaction()
        assert counts(engine) == (0, 0)
        create(session, project)
    assert counts(engine) == (1, 1)
