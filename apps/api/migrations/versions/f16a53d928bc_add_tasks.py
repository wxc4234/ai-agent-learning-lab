"""Add workspace tasks and optional conversation association."""

import sqlalchemy as sa
from alembic import op


revision = "f16a53d928bc"
down_revision = "e05f42c817ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 先创建被引用的 Task 表，再给 Conversation 添加任务外键。
    # 本次不创建默认任务，也不改变已有项目和会话的归属。
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "external_id",
            sa.String(100),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "title",
            sa.String(200),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200",
            name="ck_tasks_title_length",
        ),
    )

    # 对外标识唯一；项目索引用于后续查询某个项目下的任务。
    op.create_index(
        "ix_tasks_external_id",
        "tasks",
        ["external_id"],
        unique=True,
    )
    op.create_index(
        "ix_tasks_workspace_id",
        "tasks",
        ["workspace_id"],
    )

    # 新字段允许 NULL，使已有会话和旧版创建逻辑继续有效。
    # 不设置默认 Task，也不根据标题或用户猜测关联关系。
    op.add_column(
        "conversations",
        sa.Column(
            "task_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_conversations_task_id_tasks",
        "conversations",
        "tasks",
        ["task_id"],
        ["id"],
    )

    # PostgreSQL 普通唯一索引允许多条 NULL。
    # 因此旧会话可以同时保持未关联，但同一 Task 只能被关联一次。
    op.create_index(
        "ix_conversations_task_id",
        "conversations",
        ["task_id"],
        unique=True,
    )


def downgrade() -> None:
    # 先解除 Conversation 对 Task 的依赖，再删除 Task 表。
    # 回退保留原会话和消息，但会丢失任务及其关联，仅在隔离库演练。
    op.drop_index(
        "ix_conversations_task_id",
        table_name="conversations",
    )
    op.drop_constraint(
        "fk_conversations_task_id_tasks",
        "conversations",
        type_="foreignkey",
    )
    op.drop_column(
        "conversations",
        "task_id",
    )

    op.drop_index(
        "ix_tasks_workspace_id",
        table_name="tasks",
    )
    op.drop_index(
        "ix_tasks_external_id",
        table_name="tasks",
    )
    op.drop_table("tasks")
