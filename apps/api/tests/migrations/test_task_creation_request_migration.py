"""隔离 PostgreSQL 中验证请求记录迁移、约束与删除语义。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import TaskCreationRequest
from app.services.tasks.task_deletion_service import delete_workspace_task
from tests.migrations.test_database_readiness import migrate


@pytest.fixture
def migrated(empty_engine):
    migrate(empty_engine, 'f16a53d928bc')
    # 先提交历史数据，再升级，不能只验证空库建表。
    with empty_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (external_id) VALUES ('owner'),('other')"))
        connection.execute(text("INSERT INTO workspaces (external_id,user_id,name) VALUES ('project',1,'项目'),('other-project',2,'其他项目')"))
        connection.execute(text("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('task',1,'旧任务')"))
        connection.execute(text("INSERT INTO conversations (external_id,user_id,task_id) VALUES ('conversation',1,1)"))
        old = MetaData()
        old.reflect(connection)
        before = snapshot(connection, old)
    migrate(empty_engine)
    return empty_engine, old, before


def snapshot(connection, metadata):
    return {name: connection.execute(select(table)).all() for name, table in metadata.tables.items() if name != 'alembic_version'}


def insert_receipt(connection, **overrides):
    values = {'user_id': 1, 'workspace_id': 1, 'request_key': 'a' * 32, 'request_hash': 'b' * 64, 'task_id': 1}
    values.update(overrides)
    connection.execute(TaskCreationRequest.__table__.insert().values(**values))


def test_migration_round_trip(migrated):
    engine, old, before = migrated
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    with engine.begin() as connection:
        assert compare_metadata(MigrationContext.configure(connection, opts={'compare_server_default': True}), Base.metadata) == []
        assert snapshot(connection, old) == before
        insert_receipt(connection)
    # 回退只在测试库演练，会丢失请求记录，但不能破坏旧业务数据。
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, 'f16a53d928bc')
        assert 'task_creation_requests' not in inspect(connection).get_table_names()
        assert snapshot(connection, old) == before
    migrate(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.execute(text('SELECT count(*) FROM task_creation_requests')).scalar_one() == 0


@pytest.mark.parametrize('overrides,state', [
    ({'request_key': 'A' * 32}, '23514'),
    ({'request_key': 'a' * 31}, '23514'),
    ({'request_key': 'g' * 32}, '23514'),
    ({'request_key': 'a' * 33}, '22001'),
    ({'request_key': None}, '23502'),
    ({'request_hash': 'B' * 64}, '23514'),
    ({'request_hash': 'b' * 63}, '23514'),
    ({'request_hash': 'b' * 65}, '22001'),
    ({'request_hash': None}, '23502'),
    ({'user_id': None}, '23502'),
    ({'workspace_id': None}, '23502'),
    ({'user_id': 999999}, '23503'),
    ({'workspace_id': 999999}, '23503'),
    ({'task_id': 999999}, '23503'),
])
def test_invalid_receipts(migrated, overrides, state):
    with pytest.raises(DBAPIError) as caught, migrated[0].begin() as connection:
        insert_receipt(connection, **overrides)
    assert caught.value.orig.sqlstate == state


def test_scope_and_orm(migrated):
    engine = migrated[0]
    with engine.begin() as connection:
        insert_receipt(connection)
        # 外键只验证存在，归属授权由后续服务实现。
        insert_receipt(connection, user_id=2, task_id=None)
        insert_receipt(connection, workspace_id=2, task_id=None)
    with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
        insert_receipt(connection, request_hash='c' * 64)
    assert caught.value.orig.sqlstate == '23505'
    with Session(engine) as session:
        row = session.scalar(select(TaskCreationRequest).where(TaskCreationRequest.task_id == 1))
        assert row.id > 0 and row.created_at.tzinfo is not None
        assert row.request_key == 'a' * 32


def test_real_deletion_keeps_key(migrated):
    engine = migrated[0]
    with engine.begin() as connection:
        insert_receipt(connection)
    # 服务提交后，从独立连接确认 SET NULL，而非只观察 ORM 内存。
    with Session(engine) as session:
        delete_workspace_task(session, user_id=1, workspace_id='project', task_id='task')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT task_id, request_key FROM task_creation_requests')).one() == (None, 'a' * 32)
        assert connection.execute(text('SELECT count(*) FROM tasks')).scalar_one() == 0
    with pytest.raises(DBAPIError) as caught, engine.begin() as connection:
        insert_receipt(connection, task_id=None)
    assert caught.value.orig.sqlstate == '23505'
