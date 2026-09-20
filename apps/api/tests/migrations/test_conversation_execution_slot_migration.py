"""执行占用存储：真实迁移、旧数据保留及独立连接约束验证。"""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ConversationExecutionSlot
from app.services.tasks.task_deletion_service import delete_workspace_task
from app.services.runtime.execution.conversation_execution_service import ConversationBusyError
from tests.migrations.test_database_readiness import migrate
from tests.migrations.test_task_creation_request_migration import snapshot
from sqlalchemy import MetaData


@pytest.fixture
def migrated(empty_engine):
    migrate(empty_engine, '0a7b64e039cd')
    # 升级前有真实历史数据，包括 running/aborted Run；不能用终态推断占用。
    with empty_engine.begin() as conn:
        conn.execute(text("INSERT INTO users (external_id) VALUES ('owner')"))
        conn.execute(text("INSERT INTO workspaces (external_id,user_id,name) VALUES ('project',1,'项目')"))
        conn.execute(text("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('task',1,'旧任务')"))
        conn.execute(text("INSERT INTO conversations (external_id,user_id,task_id) VALUES ('one',1,1),('two',1,NULL)"))
        conn.execute(text("INSERT INTO agent_runs (conversation_id,status) VALUES (2,'running'),(2,'aborted')"))
        old = MetaData()
        old.reflect(conn)
        before = snapshot(conn, old)
    migrate(empty_engine)
    return empty_engine, old, before


def insert_slot(conn, **overrides):
    conn.execute(ConversationExecutionSlot.__table__.insert().values(
        **({'conversation_id': 1, 'owner_token': 'a' * 32} | overrides),
    ))


def test_upgrade_downgrade_reupgrade_preserves_history(migrated):
    engine, old, before = migrated
    with engine.begin() as conn:
        assert compare_metadata(MigrationContext.configure(conn, opts={'compare_server_default': True}), Base.metadata) == []
        assert snapshot(conn, old) == before
        assert conn.scalar(text('SELECT count(*) FROM conversation_execution_slots')) == 0
        insert_slot(conn)
        schema = inspect(conn)
        assert schema.get_pk_constraint('conversation_execution_slots')['constrained_columns'] == ['conversation_id']
        assert schema.get_indexes('conversation_execution_slots') == []
    config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    # 回退仅在隔离库进行，不操作开发库占用。
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        command.downgrade(config, '0a7b64e039cd')
        assert 'conversation_execution_slots' not in inspect(conn).get_table_names()
        assert snapshot(conn, old) == before
    migrate(engine)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        assert conn.scalar(text('SELECT count(*) FROM conversation_execution_slots')) == 0
        assert snapshot(conn, old) == before


@pytest.mark.parametrize('values,state', [
    ({'owner_token': ''}, '23514'),
    ({'owner_token': 'a' * 31}, '23514'),
    ({'owner_token': 'a' * 33}, '22001'),
    ({'owner_token': 'A' * 32}, '23514'),
    ({'owner_token': 'g' * 32}, '23514'),
    ({'owner_token': None}, '23502'),
    ({'conversation_id': None}, '23502'),
    ({'conversation_id': 999999}, '23503'),
    ({'acquired_at': None}, '23502'),
])
def test_invalid_slot_is_rejected(migrated, values, state):
    with pytest.raises(DBAPIError) as caught, migrated[0].begin() as conn:
        insert_slot(conn, **values)
    assert caught.value.orig.sqlstate == state
    with migrated[0].connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM conversation_execution_slots')) == 0


def test_orm_commits_one_slot_per_conversation(migrated):
    engine = migrated[0]
    with Session(engine) as session, session.begin():
        session.add_all([
            ConversationExecutionSlot(conversation_id=1, owner_token='a' * 32),
            ConversationExecutionSlot(conversation_id=2, owner_token='b' * 32),
        ])
    # 独立 Session 读取，确认 ORM 真正提交且数据库生成带时区时间。
    with Session(engine) as reader:
        rows = reader.scalars(select(ConversationExecutionSlot).order_by(ConversationExecutionSlot.conversation_id)).all()
        assert len(rows) == 2
        assert all(row.acquired_at.tzinfo is not None for row in rows)
    with pytest.raises(DBAPIError) as caught, engine.begin() as conn:
        insert_slot(conn, owner_token='c' * 32)
    assert caught.value.orig.sqlstate == '23505'
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT owner_token FROM conversation_execution_slots WHERE conversation_id=1')) == 'a' * 32


def test_foreign_key_prevents_silent_conversation_deletion(migrated):
    engine = migrated[0]
    with engine.begin() as conn:
        insert_slot(conn)
    with pytest.raises(DBAPIError) as caught, engine.begin() as conn:
        conn.execute(text('DELETE FROM conversations WHERE id=1'))
    assert caught.value.orig.sqlstate == '23503'
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM conversation_execution_slots')) == 1
        assert conn.scalar(text('SELECT count(*) FROM conversations WHERE id=1')) == 1


def test_real_empty_task_delete_rolls_back_while_occupied(migrated):
    engine = migrated[0]
    with engine.begin() as conn:
        insert_slot(conn)
    with Session(engine) as session:
        with pytest.raises(ConversationBusyError):
            delete_workspace_task(session, user_id=1, workspace_id='project', task_id='task')
        assert not session.in_transaction()
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM tasks')) == 1
        assert conn.scalar(text('SELECT count(*) FROM conversations WHERE id=1')) == 1
        assert conn.scalar(text('SELECT count(*) FROM conversation_execution_slots')) == 1
    # 这里只验证结构：删除占用后原空任务可删，持有者匹配由下一课服务负责。
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM conversation_execution_slots WHERE conversation_id=1'))
    with Session(engine) as session:
        delete_workspace_task(session, user_id=1, workspace_id='project', task_id='task')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM tasks')) == 0
