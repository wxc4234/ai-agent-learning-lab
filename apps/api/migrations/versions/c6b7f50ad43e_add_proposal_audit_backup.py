"""Persist proposal transition audit and original content for explicit recovery."""
from alembic import op
import sqlalchemy as sa
revision = 'c6b7f50ad43e'
down_revision = 'b5a6e4f9c32d'
branch_labels = None
depends_on = None


def upgrade():
    # 历史记录不伪造原文或审计；新执行先保存基线，提交确认后才能写文件。
    op.add_column('file_edit_proposals', sa.Column('baseline_content', sa.Text(), nullable=True))
    op.create_check_constraint('ck_file_edit_proposals_backup_size', 'file_edit_proposals',
        'baseline_content IS NULL OR octet_length(baseline_content) <= 262144')
    op.create_table('proposal_audit_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('file_edit_proposals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('event', sa.String(40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_proposal_audit_events_proposal_id', 'proposal_audit_events', ['proposal_id'])


def downgrade():
    connection = op.get_bind()
    connection.execute(sa.text('LOCK TABLE proposal_audit_events, file_edit_proposals IN ACCESS EXCLUSIVE MODE'))
    if connection.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM proposal_audit_events) OR EXISTS(SELECT 1 FROM file_edit_proposals WHERE baseline_content IS NOT NULL)')):
        raise RuntimeError('Cannot discard proposal audit history')
    op.drop_table('proposal_audit_events')
    op.drop_constraint('ck_file_edit_proposals_backup_size', 'file_edit_proposals')
    op.drop_column('file_edit_proposals', 'baseline_content')
