"""Keep restricted sample provenance until file cleanup is confirmed."""

from alembic import op
import sqlalchemy as sa


revision = "82d14f5b09ad"
down_revision = "71c43e9a8f02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 现有来源均仍是活动绑定；默认值同时保护旧数据和直接SQL插入。
    op.add_column(
        "workspace_sample_origins",
        sa.Column(
            "lifecycle_state",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_workspace_sample_origins_lifecycle_state",
        "workspace_sample_origins",
        "lifecycle_state IN ('active', 'cleanup_pending')",
    )


def downgrade() -> None:
    # 待办未确认时删除状态列会丢失清理证据，必须先拒绝回退。
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE workspace_sample_origins IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM workspace_sample_origins "
        "WHERE lifecycle_state = 'cleanup_pending')"
    )):
        raise RuntimeError("存在受限样例清理待办，不能无损回退")
    op.drop_constraint(
        "ck_workspace_sample_origins_lifecycle_state",
        "workspace_sample_origins",
        type_="check",
    )
    op.drop_column("workspace_sample_origins", "lifecycle_state")
