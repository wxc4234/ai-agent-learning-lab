"""Persist one application claim without enabling file writes."""

from alembic import op
import sqlalchemy as sa

revision = '60d32ae695cd'
down_revision = '5fc219d584bc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史审批保持原样，应用状态独立初始化为未开始。
    op.add_column('file_edit_proposals', sa.Column('application_status', sa.String(20), server_default='idle', nullable=False))
    op.add_column('file_edit_proposals', sa.Column('application_token', sa.String(32), nullable=True))
    op.create_check_constraint('ck_file_edit_proposals_application_status', 'file_edit_proposals',
                               "application_status IN ('idle', 'running', 'applied', 'not_applied', 'uncertain')")
    op.create_check_constraint('ck_file_edit_proposals_application_token', 'file_edit_proposals',
                               "(application_status = 'idle' AND application_token IS NULL) OR "
                               "(application_status != 'idle' AND status = 'approved' "
                               "AND application_token IS NOT NULL AND application_token ~ '^[0-9a-f]{32}$')")


def downgrade() -> None:
    # 锁内检查，拒绝丢失执行记录或把未知结果变成可重新执行。
    connection = op.get_bind()
    connection.execute(sa.text('LOCK TABLE file_edit_proposals IN ACCESS EXCLUSIVE MODE'))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM file_edit_proposals WHERE application_status != 'idle')")):
        raise RuntimeError('存在应用执行记录，不能无损回退')
    op.drop_constraint('ck_file_edit_proposals_application_token', 'file_edit_proposals', type_='check')
    op.drop_constraint('ck_file_edit_proposals_application_status', 'file_edit_proposals', type_='check')
    op.drop_column('file_edit_proposals', 'application_token')
    op.drop_column('file_edit_proposals', 'application_status')
