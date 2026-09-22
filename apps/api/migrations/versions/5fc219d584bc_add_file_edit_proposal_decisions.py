"""Allow explicit approval and rejection without enabling file writes."""

from alembic import op
import sqlalchemy as sa


revision = "5fc219d584bc"
down_revision = "4eb108c473ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 既有记录都是pending，可以原样保留，不需要改写业务数据。
    op.drop_constraint(
        "ck_file_edit_proposals_status",
        "file_edit_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_edit_proposals_status",
        "file_edit_proposals",
        "status IN ('pending', 'approved', 'rejected')",
    )
    op.create_check_constraint(
        "ck_file_edit_proposals_approval_diff",
        "file_edit_proposals",
        "status != 'approved' OR diff_truncated = false",
    )


def downgrade() -> None:
    connection = op.get_bind()

    # 在迁移事务内阻止并发写入，避免检查后又出现新的审批记录。
    connection.execute(
        sa.text("LOCK TABLE file_edit_proposals IN ACCESS EXCLUSIVE MODE")
    )
    has_decisions = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM file_edit_proposals WHERE status != 'pending'"
            ")"
        )
    )
    if has_decisions:
        # 不把用户决策静默改回pending，也不删除记录来强行回退。
        raise RuntimeError("存在已审批提案，不能无损回退到仅支持pending的版本")

    op.drop_constraint(
        "ck_file_edit_proposals_approval_diff",
        "file_edit_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_file_edit_proposals_status",
        "file_edit_proposals",
        type_="check",
    )
    op.create_check_constraint(
        "ck_file_edit_proposals_status",
        "file_edit_proposals",
        "status = 'pending'",
    )
