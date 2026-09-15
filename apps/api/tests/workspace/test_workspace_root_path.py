"""目录字段的模型契约，防止约束误放到其他表。"""

from sqlalchemy import CheckConstraint, Text

from app.models import User, Workspace


def checks(model):
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_workspace_root_path_is_optional_without_implicit_binding():
    column = Workspace.__table__.c.root_path
    assert isinstance(column.type, Text)
    assert column.nullable
    assert column.default is None
    assert column.server_default is None


def test_workspace_owns_name_and_root_path_constraints():
    constraints = checks(Workspace)
    assert constraints == {
        "ck_workspaces_name_length": "char_length(name) BETWEEN 1 AND 100",
        "ck_workspaces_root_path_not_empty": "root_path IS NULL OR char_length(root_path) > 0",
    }


def test_user_schema_retains_existing_constraint_without_workspace_columns():
    # 本地身份同样使用 users 表；这里验证迁移兼容性，不运行账号流程。
    constraints = checks(User)
    assert constraints == {
        "ck_users_login_credentials_pair": (
            "(username IS NULL AND password_hash IS NULL) OR "
            "(username IS NOT NULL AND password_hash IS NOT NULL)"
        ),
    }
    assert "root_path" not in User.__table__.c
    assert "name" not in User.__table__.c


def test_real_migration_preserves_rows_and_enforces_constraint(empty_engine):
    import pytest
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import MetaData, Table, inspect, select, text
    from sqlalchemy.exc import IntegrityError

    from tests.migrations.test_workspace_migration import load_migration, metadata_before_root_path, metadata_before_tasks

    migration = load_migration("e05f42c817ab_add_workspace_root_path.py")
    assert migration.down_revision == "d94e31b706fa"
    assert migration.revision == "e05f42c817ab"
    # 用真实旧迁移构造升级前数据库，提交后再开启升级事务。
    with empty_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for name in (
                "fed4e53cb0f7_create_agent_schema.py",
                "a91c42e7d603_add_user_login_credentials.py",
                "b62d19f804ae_normalize_conversation_unique_index.py",
                "c83f20a915bd_add_login_sessions.py",
                "d94e31b706fa_add_workspaces.py",
            ):
                load_migration(name).upgrade()
        connection.execute(text("INSERT INTO users (id, external_id) VALUES (1, 'local-owner-v1')"))
        connection.execute(text("INSERT INTO workspaces (external_id, user_id, name) VALUES ('legacy', 1, '旧项目')"))
        old = Table("workspaces", MetaData(), autoload_with=connection)
        old_row = connection.execute(select(old)).one()._asdict()
        owner_before = connection.execute(text("SELECT * FROM users")).all()
    with empty_engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    with empty_engine.begin() as connection:
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        assert compare_metadata(context, metadata_before_tasks()) == []
        column = next(c for c in inspect(connection).get_columns("workspaces") if c["name"] == "root_path")
        assert column["nullable"] and column["default"] is None
        assert isinstance(column["type"], Text)
        row = connection.execute(select(Workspace.__table__)).one()._asdict()
        assert row.pop("root_path") is None
        assert row == old_row
        assert connection.execute(text("SELECT * FROM users")).all() == owner_before
        # savepoint 保留真实 PostgreSQL 约束失败后的外层事务可用性。
        with pytest.raises(IntegrityError) as caught, connection.begin_nested():
            connection.execute(text("UPDATE workspaces SET root_path = ''"))
        assert caught.value.orig.diag.constraint_name == "ck_workspaces_root_path_not_empty"
        connection.execute(text("UPDATE workspaces SET root_path = :path"), {"path": "/项目/ 中文目录 "})
        connection.execute(text("INSERT INTO workspaces (external_id, user_id, name) VALUES ('new', 1, '新项目')"))
    with empty_engine.connect() as connection:
        rows = connection.execute(select(Workspace.__table__).order_by(Workspace.id)).all()
        assert [r.root_path for r in rows] == ["/项目/ 中文目录 ", None]
    # 回退仅删除目录字段，保留原工作空间及用户；再次升级恢复 NULL。
    with empty_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "root_path" not in {c["name"] for c in inspect(connection).get_columns("workspaces")}
        assert compare_metadata(MigrationContext.configure(connection), metadata_before_root_path()) == []
        assert connection.execute(select(old).where(old.c.external_id == "legacy")).one()._asdict() == old_row
        assert connection.execute(text("SELECT * FROM users")).all() == owner_before
    with empty_engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    with empty_engine.connect() as connection:
        assert connection.execute(text("SELECT root_path FROM workspaces")).scalars().all() == [None, None]
        assert compare_metadata(MigrationContext.configure(connection), metadata_before_tasks()) == []
