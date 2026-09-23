"""Persist verified directory identities for newly created restricted samples."""

from alembic import op
import sqlalchemy as sa


revision = "93e4c2d7a10b"
down_revision = "82d14f5b09ad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 不设置默认值或回填：旧来源没有创建时的描述符证据。
    for column_name in ("parent_dev", "parent_ino", "root_dev", "root_ino"):
        op.add_column(
            "workspace_sample_origins",
            sa.Column(column_name, sa.BigInteger(), nullable=True),
        )

    op.create_check_constraint(
        "ck_workspace_sample_origins_directory_identity",
        "workspace_sample_origins",
        "(parent_dev IS NULL AND parent_ino IS NULL "
        "AND root_dev IS NULL AND root_ino IS NULL) OR "
        "(parent_dev IS NOT NULL AND parent_ino IS NOT NULL "
        "AND root_dev IS NOT NULL AND root_ino IS NOT NULL "
        "AND parent_dev >= 0 AND parent_ino > 0 "
        "AND root_dev >= 0 AND root_ino > 0)",
    )


def downgrade() -> None:
    # 降级会丢失身份依据；锁表后只允许全部身份仍为空时回退。
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE workspace_sample_origins IN ACCESS EXCLUSIVE MODE")
    )
    has_identity = connection.scalar(sa.text(
        "SELECT EXISTS ("
        "SELECT 1 FROM workspace_sample_origins "
        "WHERE parent_dev IS NOT NULL OR parent_ino IS NOT NULL "
        "OR root_dev IS NOT NULL OR root_ino IS NOT NULL"
        ")"
    ))
    if has_identity:
        raise RuntimeError("存在受限样例目录身份记录，不能无损回退")

    op.drop_constraint(
        "ck_workspace_sample_origins_directory_identity",
        "workspace_sample_origins",
        type_="check",
    )
    for column_name in ("root_ino", "root_dev", "parent_ino", "parent_dev"):
        op.drop_column("workspace_sample_origins", column_name)
