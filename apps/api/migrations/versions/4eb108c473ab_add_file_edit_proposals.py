"""Add pending file edit proposals without enabling file writes."""

from alembic import op
import sqlalchemy as sa


revision = "4eb108c473ab"
down_revision = "3da097b362fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_edit_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(32), nullable=False),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bound_root", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.String(4096), nullable=False),
        sa.Column("baseline_sha256", sa.String(64), nullable=False),
        sa.Column("proposed_content", sa.Text(), nullable=False),
        sa.Column("proposed_sha256", sa.String(64), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("diff_truncated", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status = 'pending'",
            name="ck_file_edit_proposals_status",
        ),
        sa.CheckConstraint(
            "char_length(bound_root) BETWEEN 1 AND 4096",
            name="ck_file_edit_proposals_root",
        ),
        sa.CheckConstraint(
            "char_length(relative_path) BETWEEN 1 AND 4096",
            name="ck_file_edit_proposals_path",
        ),
        sa.CheckConstraint(
            "baseline_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_file_edit_proposals_baseline",
        ),
        sa.CheckConstraint(
            "proposed_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_file_edit_proposals_proposed_hash",
        ),
        sa.CheckConstraint(
            "octet_length(proposed_content) <= 262144",
            name="ck_file_edit_proposals_content_size",
        ),
        sa.CheckConstraint(
            "char_length(diff) BETWEEN 1 AND 16384",
            name="ck_file_edit_proposals_diff_size",
        ),
    )
    op.create_index(
        "ix_file_edit_proposals_external_id",
        "file_edit_proposals",
        ["external_id"],
        unique=True,
    )
    op.create_index(
        "ix_file_edit_proposals_task_id",
        "file_edit_proposals",
        ["task_id"],
    )


def downgrade() -> None:
    # 回退会删除提案数据，不涉及任何项目文件。
    op.drop_index(
        "ix_file_edit_proposals_task_id",
        table_name="file_edit_proposals",
    )
    op.drop_index(
        "ix_file_edit_proposals_external_id",
        table_name="file_edit_proposals",
    )
    op.drop_table("file_edit_proposals")
