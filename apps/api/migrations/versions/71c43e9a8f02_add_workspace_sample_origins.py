"""Persist restricted sample provenance without restoring execution authority."""

from alembic import op
import sqlalchemy as sa


revision = "71c43e9a8f02"
down_revision = "60d32ae695cd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧项目目录一律不回填来源；只有服务端新建样例才写入该表。
    op.create_table(
        "workspace_sample_origins",
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id"),
        sa.UniqueConstraint("task_id", name="uq_workspace_sample_origins_task_id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.CheckConstraint("char_length(root_path) > 0", name="ck_workspace_sample_origins_root_path_not_empty"),
    )


def downgrade() -> None:
    # 有来源记录时拒绝丢失证据；只允许在空表上回退。
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE workspace_sample_origins IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM workspace_sample_origins)")):
        raise RuntimeError("存在受限样例来源记录，不能无损回退")
    op.drop_table("workspace_sample_origins")
