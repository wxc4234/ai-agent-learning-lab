"""Exercise the real migration chain and workspace rollback only in isolation."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, inspect, select

from app.database import Base


def load_migration(filename):
    path = Path(__file__).resolve().parents[2] / "migrations/versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def metadata_before_tasks():
    # 历史版本没有 Task 表和会话关联字段，移除其索引与外键后再比较。
    snapshot = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in {"tasks", "task_creation_requests", "conversation_execution_slots"}:
            table.to_metadata(snapshot)
    conversation = snapshot.tables["conversations"]
    for constraint in list(conversation.constraints):
        if any(column.name == "task_id" for column in constraint.columns):
            conversation.constraints.remove(constraint)
    for index in list(conversation.indexes):
        if "task_id" in index.columns:
            conversation.indexes.remove(index)
    for key in list(conversation.foreign_keys):
        if key.parent.name == "task_id":
            conversation.foreign_keys.remove(key)
    conversation._columns.remove(conversation.c.task_id)
    return snapshot


def metadata_before_root_path():
    # 历史迁移应与该版本快照比较，不能直接与持续演进的最新模型比较。
    snapshot = metadata_before_tasks()
    workspace = snapshot.tables["workspaces"]
    for constraint in list(workspace.constraints):
        if constraint.name == "ck_workspaces_root_path_not_empty":
            workspace.constraints.remove(constraint)
    workspace._columns.remove(workspace.c.root_path)
    return snapshot


def test_workspace_migration_preserves_existing_data_and_matches_metadata(empty_engine):
    previous = [
        "fed4e53cb0f7_create_agent_schema.py",
        "a91c42e7d603_add_user_login_credentials.py",
        "b62d19f804ae_normalize_conversation_unique_index.py",
        "c83f20a915bd_add_login_sessions.py",
    ]
    migration = load_migration("d94e31b706fa_add_workspaces.py")
    assert migration.down_revision == "c83f20a915bd"
    metadata = metadata_before_root_path()
    old_tables = [table for table in metadata.sorted_tables if table.name != "workspaces"]
    with empty_engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            # 从真实旧迁移链建立基线，避免用最新模型伪造旧数据库。
            for filename in previous:
                load_migration(filename).upgrade()
            now = datetime.now(timezone.utc)
            seed = {
                "users": {"id": 1, "external_id": "legacy-user"},
                "conversations": {"id": 1, "user_id": 1, "external_id": "legacy-chat"},
                "messages": {"id": 1, "conversation_id": 1, "role": "user", "content": "历史消息"},
                "agent_runs": {"id": 1, "conversation_id": 1, "status": "done"},
                "agent_run_events": {"id": 1, "run_id": 1, "event_type": "RUN_FINISHED", "payload": {"legacy": True}},
                "login_sessions": {"id": 1, "user_id": 1, "token_hash": "a" * 64, "created_at": now, "expires_at": now + timedelta(hours=1)},
            }
            for table in old_tables:
                connection.execute(table.insert().values(**seed[table.name]))
            # 比较完整旧表记录，而不仅是行数，防止升级悄悄改写历史值。
            before = {table.name: connection.execute(select(table)).all() for table in old_tables}
            assert "workspaces" not in inspect(connection).get_table_names()
            migration.upgrade()
            assert compare_metadata(context, metadata) == []
            workspace_table = metadata.tables["workspaces"]
            connection.execute(workspace_table.insert().values(external_id="workspace", user_id=1, name="迁移项目"))
            record = connection.execute(select(workspace_table)).one()
            assert record.created_at.tzinfo is not None
            # 回退和再次升级仅发生在本例随机隔离 schema 内。
            migration.downgrade()
            assert "workspaces" not in inspect(connection).get_table_names()
            assert {table.name: connection.execute(select(table)).all() for table in old_tables} == before
            migration.upgrade()
            assert compare_metadata(context, metadata) == []
            assert connection.execute(select(workspace_table)).all() == []
            assert {table.name: connection.execute(select(table)).all() for table in old_tables} == before


def test_existing_login_schema_can_reconcile_version_then_upgrade(empty_engine):
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import MetaData

    script = ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")))
    expected_before = MetaData()
    for table in metadata_before_root_path().sorted_tables:
        if table.name != "workspaces":
            table.to_metadata(expected_before)
    with empty_engine.begin() as connection:
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        with Operations.context(context):
            for filename in (
                "fed4e53cb0f7_create_agent_schema.py",
                "a91c42e7d603_add_user_login_credentials.py",
                "b62d19f804ae_normalize_conversation_unique_index.py",
                "c83f20a915bd_add_login_sessions.py",
            ):
                load_migration(filename).upgrade()
            # 模拟结构已更新但账本仍落后的状态，校准前必须比对完整目标结构。
            context.stamp(script, "b62d19f804ae")
            assert compare_metadata(context, expected_before) == []
            context.stamp(script, "c83f20a915bd")
            load_migration("d94e31b706fa_add_workspaces.py").upgrade()
            context.stamp(script, "d94e31b706fa")
            assert context.get_current_heads() == ("d94e31b706fa",)
            assert compare_metadata(context, metadata_before_root_path()) == []
