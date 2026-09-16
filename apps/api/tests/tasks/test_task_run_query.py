"""运行概要：真实 PostgreSQL 授权、游标分页与只读事务边界。"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentRun, AgentRunEvent, Conversation, Workspace
from app.repositories.workspace.workspace_repository import WorkspaceNotAccessibleError
from app.services.tasks import task_run_query as service
import tests.tasks.test_task_deletion_service as deletion_tests


# 共用同一套任务/会话归属数据，底层仍使用根 PostgreSQL 隔离夹具。
target = deletion_tests.target


@pytest.fixture
def database(engine, monkeypatch):
    monkeypatch.setattr(settings, 'app_mode', 'local')
    sessions = []
    statements = []

    class TrackedSession(Session):
        closed = False

        def commit(self):
            pytest.fail('read-only service must not commit')

        def close(self):
            super().close()
            self.closed = True

    def factory():
        session = TrackedSession(engine)
        sessions.append(session)
        return session

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    monkeypatch.setattr(service, 'SessionLocal', factory)
    event.listen(engine, 'before_cursor_execute', capture)
    yield sessions, statements
    event.remove(engine, 'before_cursor_execute', capture)
    assert all(session.closed and not session.in_transaction() for session in sessions)


def read(target, **overrides):
    args = {key: target[key] for key in ('user_id', 'workspace_id', 'task_id')}
    return service.list_task_runs(**(args | overrides))


def seed(engine, target, count, *, status='done', finished=True):
    start = datetime(2026, 9, 16, tzinfo=timezone.utc)
    with Session(engine) as session, session.begin():
        rows = [AgentRun(
            conversation_id=target['conversation_pk'], status=status,
            started_at=start, finished_at=start + timedelta(milliseconds=1234) if finished else None,
        ) for _ in range(count)]
        session.add_all(rows)
        session.flush()
        return [row.id for row in rows]


def test_empty_owned_task_is_plain_result_after_session_close(database, target):
    result = read(target)
    assert result.model_dump(mode='json') == {
        'workspace_id': target['workspace_id'], 'task_id': target['task_id'],
        'items': [], 'next_cursor': None,
    }
    assert database[0][0].closed


@pytest.mark.parametrize('count,limit', [(1, 1), (2, 2), (3, 2), (5, 2), (51, 50)])
def test_pages_have_no_gaps_or_duplicates(engine, database, target, count, limit):
    ids = seed(engine, target, count)
    seen = []
    before = None
    while True:
        result = read(target, limit=limit, before=before)
        page = [item.run_id for item in result.items]
        seen.extend(page)
        if result.next_cursor is None:
            break
        assert len(page) == limit
        assert result.next_cursor == str(page[-1])
        before = int(result.next_cursor)
    assert seen == ids[::-1]


def test_cursor_is_boundary_not_resource_and_new_rows_need_refresh(engine, database, target):
    ids = seed(engine, target, 5)
    first = read(target, limit=2)
    with Session(engine) as session, session.begin():
        session.delete(session.get(AgentRun, ids[-2]))
    new = seed(engine, target, 1)[0]
    assert [item.run_id for item in read(target, before=int(first.next_cursor)).items] == ids[-3::-1]
    assert read(target).items[0].run_id == new
    assert read(target, before=1).items == []
    assert read(target, before=service.MAX_RUN_ID).items[0].run_id == new


@pytest.mark.parametrize('status', ['running', 'done', 'error', 'aborted', 'legacy-state'])
@pytest.mark.parametrize('finished', [False, True])
def test_state_and_timestamps_are_not_invented(engine, database, target, status, finished):
    seed(engine, target, 1, status=status, finished=finished)
    item = read(target).items[0]
    assert item.status == status
    assert item.duration_ms == (1234 if finished else None)
    assert (item.finished_at is not None) is finished
    assert item.started_at.tzinfo is not None
    assert set(item.model_dump()) == {'run_id', 'status', 'started_at', 'finished_at', 'duration_ms'}


def test_filters_conversation_and_does_not_read_events_or_write(engine, database, target):
    ids = seed(engine, target, 1)
    with Session(engine) as session, session.begin():
        sibling = session.scalar(select(Conversation).where(Conversation.external_id == 'e' * 32))
        session.add(AgentRun(conversation_id=sibling.id, status='running'))
        session.add(AgentRunEvent(run_id=ids[0], event_type='PRIVATE', payload={'secret': 'PRIVATE'}))
    database[1].clear()
    result = read(target)
    assert [item.run_id for item in result.items] == ids
    assert 'PRIVATE' not in result.model_dump_json()
    assert all(sql.lstrip().startswith('select') for sql in database[1])
    assert all('agent_run_events' not in sql and 'for update' not in sql for sql in database[1])
    assert len([sql for sql in database[1] if 'agent_runs' in sql]) == 1
    with Session(engine) as session:
        assert session.scalar(select(AgentRunEvent.payload)) == {'secret': 'PRIVATE'}


@pytest.mark.parametrize('kind', ['missing-workspace', 'missing-task', 'foreign-owner', 'wrong-project', 'foreign-conversation', 'missing-conversation'])
def test_authorize_even_without_runs(engine, database, target, kind):
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
    database[1].clear()
    with pytest.raises(WorkspaceNotAccessibleError):
        read(target, **overrides)
    assert all('agent_runs' not in sql for sql in database[1])


@pytest.mark.parametrize('field,value', [
    ('workspace_id', None), ('workspace_id', 'A' * 32), ('workspace_id', 'a' * 32 + '\n'),
    ('task_id', 1), ('task_id', ''), ('task_id', '../path'),
    ('limit', True), ('limit', '20'), ('limit', 1.0), ('limit', None), ('limit', 0), ('limit', 51),
    ('before', True), ('before', '1'), ('before', 1.0), ('before', 0), ('before', -1), ('before', 2_147_483_648),
])
def test_bad_input_rejected_before_opening_database(monkeypatch, field, value):
    def forbidden():
        pytest.fail('invalid input must not open a Session')
    monkeypatch.setattr(service, 'SessionLocal', forbidden)
    with pytest.raises(service.InvalidTaskRunQueryError) as exc:
        read({'user_id': 1, 'workspace_id': 'a' * 32, 'task_id': 'b' * 32}, **{field: value})
    assert exc.value.code == 'invalid_task_run_query'


def test_database_error_is_not_disguised_as_empty_page(engine, database, target):
    def break_query(conn, cursor, statement, parameters, context, executemany):
        if 'agent_runs' in statement:
            return 'SELECT 1 / 0', ()
        return statement, parameters
    event.listen(engine, 'before_cursor_execute', break_query, retval=True)
    try:
        with pytest.raises(DBAPIError):
            read(target)
    finally:
        event.remove(engine, 'before_cursor_execute', break_query)
    assert database[0][-1].closed


def test_invalid_persisted_duration_fails_and_closes_session(engine, database, target):
    ids = seed(engine, target, 1)
    with Session(engine) as session, session.begin():
        row = session.get(AgentRun, ids[0])
        row.finished_at = row.started_at - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        read(target)
    assert database[0][-1].closed
