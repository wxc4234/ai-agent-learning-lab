"""Add durable request keys for idempotent task creation."""

import sqlalchemy as sa
from alembic import op


revision = "0a7b64e039cd"
down_revision = "f16a53d928bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 只增加新表；历史 Task 没有请求键，不猜测或回填创建意图。
    # 建表、约束和索引由 Alembic 的迁移事务统一管理。
    op.create_table(
        "task_creation_requests",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "request_key",
            sa.String(32),
            nullable=False,
        ),
        sa.Column(
            "request_hash",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey(
                "tasks.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "workspace_id",
            "request_key",
            name="uq_task_creation_requests_scope_key",
        ),
        sa.CheckConstraint(
            "request_key ~ '^[0-9a-f]{32}$'",
            name="ck_task_creation_requests_key",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_task_creation_requests_hash",
        ),
    )

    # 支持按项目查询，以及删除 Task 时定位需要置空的引用。
    op.create_index(
        "ix_task_creation_requests_workspace_id",
        "task_creation_requests",
        ["workspace_id"],
    )
    op.create_index(
        "ix_task_creation_requests_task_id",
        "task_creation_requests",
        ["task_id"],
    )


def downgrade() -> None:
    # 回退会丢失幂等记录，之后无法继续识别旧请求。
    # 仅在隔离迁移测试中演练，不把它当成普通重试或清理操作。
    op.drop_index(
        "ix_task_creation_requests_task_id",
        table_name="task_creation_requests",
    )
    op.drop_index(
        "ix_task_creation_requests_workspace_id",
        table_name="task_creation_requests",
    )
    op.drop_table("task_creation_requests")