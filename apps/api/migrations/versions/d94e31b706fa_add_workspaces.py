"""Add owned workspaces without changing existing conversations or users."""

import sqlalchemy as sa
from alembic import op

revision = "d94e31b706fa"
down_revision = "c83f20a915bd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 只新增独立表，不回填或修改现有用户、会话与运行数据。
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100",
            name="ck_workspaces_name_length",
        ),
    )
    op.create_index("ix_workspaces_external_id", "workspaces", ["external_id"], unique=True)
    op.create_index("ix_workspaces_user_id", "workspaces", ["user_id"])


def downgrade() -> None:
    # 回退会删除工作空间数据，只在隔离测试库演练，不用于开发库恢复。
    op.drop_index("ix_workspaces_user_id", table_name="workspaces")
    op.drop_index("ix_workspaces_external_id", table_name="workspaces")
    op.drop_table("workspaces")
