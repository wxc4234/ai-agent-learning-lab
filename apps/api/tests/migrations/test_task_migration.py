"""Task 的真实增量迁移、约束与 ORM 持久化，仅使用隔离 PostgreSQL。"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Conversation, Task, User, Workspace
from tests.migrations.test_workspace_migration import load_migration, metadata_before_tasks


def metadata_at_task_revision():
    # 历史修订只比较当时的表，后续迁移由各自测试验证。
    snapshot = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in {"task_creation_requests", "conversation_execution_slots"}:
            table.to_metadata(snapshot)
    return snapshot


@pytest.fixture
def migrated(empty_engine):
    # 从真实旧迁移建立上个版本，历史数据先提交，再单独运行新迁移。
    with empty_engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        for filename in (
            'fed4e53cb0f7_create_agent_schema.py',
            'a91c42e7d603_add_user_login_credentials.py',
            'b62d19f804ae_normalize_conversation_unique_index.py',
            'c83f20a915bd_add_login_sessions.py',
            'd94e31b706fa_add_workspaces.py',
            'e05f42c817ab_add_workspace_root_path.py',
        ):
            load_migration(filename).upgrade()
        connection.execute(text("INSERT INTO users (external_id) VALUES ('legacy-owner')"))
        connection.execute(text("INSERT INTO workspaces (external_id,user_id,name,root_path) VALUES ('legacy-project',1,'旧项目','/existing')"))
        connection.execute(text("INSERT INTO conversations (external_id,user_id,title) VALUES ('legacy-a',1,'旧对话'),('legacy-b',1,NULL)"))
        connection.execute(text("INSERT INTO messages (conversation_id,role,content) VALUES (1,'user','历史消息')"))
        connection.execute(text("INSERT INTO agent_runs (conversation_id,status) VALUES (1,'done')"))
        connection.execute(text("INSERT INTO agent_run_events (run_id,event_type,payload) VALUES (1,'RUN_FINISHED','{}')"))
        old = MetaData()
        old.reflect(connection)
        before = {name: connection.execute(select(table).order_by(table.c.id)).all() for name, table in old.tables.items()}
    migration = load_migration('f16a53d928bc_add_tasks.py')
    assert migration.down_revision == 'e05f42c817ab'
    assert migration.revision == 'f16a53d928bc'
    with empty_engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    return empty_engine, old, before, migration


def test_old_data_round_trip_and_metadata(migrated):
    engine, old, before, migration = migrated
    with engine.begin() as connection:
        assert compare_metadata(MigrationContext.configure(connection, opts={'compare_server_default': True}), metadata_at_task_revision()) == []
        assert {name: connection.execute(select(table).order_by(table.c.id)).all() for name, table in old.tables.items()} == before
        assert connection.execute(text('SELECT task_id FROM conversations ORDER BY id')).scalars().all() == [None, None]
        connection.execute(text("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('new-task',1,'新任务')"))
        connection.execute(text('UPDATE conversations SET task_id=1 WHERE id=1'))
    # 真实保存的任务被回退移除，但原会话、消息、事件及目录绑定保持不变。
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        assert compare_metadata(MigrationContext.configure(connection), metadata_before_tasks()) == []
        assert {name: connection.execute(select(table).order_by(table.c.id)).all() for name, table in old.tables.items()} == before
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata_at_task_revision()) == []
        assert connection.execute(text('SELECT count(*) FROM tasks')).scalar_one() == 0
        assert connection.execute(text('SELECT task_id FROM conversations')).scalars().all() == [None, None]


@pytest.mark.parametrize('sql,sqlstate', [
    ("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('bad',999999,'任务')", '23503'),
    ("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('bad',NULL,'任务')", '23502'),
    ("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('bad',1,'')", '23514'),
    ("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('bad',1,repeat('字',201))", '22001'),
    ("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('bad',1,NULL)", '23502'),
    ("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('task',1,'重复标识')", '23505'),
    ("UPDATE conversations SET task_id=999999 WHERE id=2", '23503'),
    ("UPDATE conversations SET task_id=1 WHERE id=2", '23505'),
])
def test_database_rejects_invalid_associations(migrated, sql, sqlstate):
    engine = migrated[0]
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tasks (external_id,workspace_id,title) VALUES ('task',1,'任务')"))
        connection.execute(text('UPDATE conversations SET task_id=1 WHERE id=1'))
    with pytest.raises(IntegrityError if sqlstate != '22001' else DataError) as caught, engine.begin() as connection:
        connection.execute(text(sql))
    assert caught.value.orig.sqlstate == sqlstate
    with engine.connect() as connection:
        assert connection.execute(text('SELECT task_id FROM conversations ORDER BY id')).scalars().all() == [1, None]


def test_orm_relationships_commit_reload_and_legacy_creation(migrated):
    engine = migrated[0]
    with Session(engine) as session:
        workspace = session.get(Workspace, 1)
        user = session.get(User, 1)
        task = Task(external_id='orm-task', title='😀' * 200, workspace=workspace)
        conversation = Conversation(external_id='orm-conversation', user=user, task=task)
        legacy = Conversation(external_id='old-client', user_id=1)
        session.add_all([task, conversation, legacy])
        session.commit()
    with Session(engine) as session:
        task = session.scalar(select(Task).where(Task.external_id == 'orm-task'))
        assert task.workspace.tasks == [task]
        assert task.conversation.task is task
        assert task.created_at.tzinfo is not None
        assert session.scalar(select(Conversation).where(Conversation.external_id == 'old-client')).task is None
        # 同名任务合法，只有独立标识唯一。
        session.add(Task(external_id='same-title', title=task.title, workspace_id=1))
        session.commit()


def test_recover_empty_create_all_table_then_upgrade(migrated):
    engine, old, before, migration = migrated
    # 重现开发启动器提前 create_all：新表已出现，但旧会话缺少关联列。
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        Task.__table__.create(connection)
    with engine.begin() as connection:
        connection.execute(text('LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE'))
        assert connection.execute(text('SELECT count(*) FROM tasks')).scalar_one() == 0
        Task.__table__.drop(connection)
        assert compare_metadata(MigrationContext.configure(connection), metadata_before_tasks()) == []
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert compare_metadata(MigrationContext.configure(connection), metadata_at_task_revision()) == []
        assert {name: connection.execute(select(table).order_by(table.c.id)).all() for name, table in old.tables.items()} == before
