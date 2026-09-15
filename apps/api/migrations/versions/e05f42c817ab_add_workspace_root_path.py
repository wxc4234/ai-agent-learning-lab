"""Add an optional project directory to existing workspaces."""

import sqlalchemy as sa
from alembic import op


revision = "e05f42c817ab"
down_revision = "d94e31b706fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加可空字段，不回填目录；已有工作空间继续保持未绑定。
    # 不设置 server_default，避免将同一路径自动赋给所有记录。
    op.add_column(
        "workspaces",
        sa.Column(
            "root_path",
            sa.Text(),
            nullable=True,
        ),
    )

    # 数据库只兜底拒绝空字符串，实际目录校验由绑定服务执行。
    op.create_check_constraint(
        "ck_workspaces_root_path_not_empty",
        "workspaces",
        "root_path IS NULL OR char_length(root_path) > 0",
    )


def downgrade() -> None:
    # 先删除依赖该列的约束，再删除列。
    # 回退会丢失目录绑定信息，仅在隔离测试库演练。
    op.drop_constraint(
        "ck_workspaces_root_path_not_empty",
        "workspaces",
        type_="check",
    )
    op.drop_column(
        "workspaces",
        "root_path",
    )
