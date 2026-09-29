"""Persist multi-file approval, recovery journals and owned work areas.

Revision ID: d7c8a61be54f
Revises: c6b7f50ad43e
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = 'd7c8a61be54f'
down_revision = 'c6b7f50ad43e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('task_change_sets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('external_id', sa.String(32), nullable=False, unique=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('bound_root', sa.Text(), nullable=False),
        sa.Column('binding_revision', sa.BigInteger(), nullable=False),
        sa.Column('root_identity', JSONB(), nullable=False), sa.Column('entries', JSONB(), nullable=False),
        sa.Column('diff', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('journal', JSONB(), nullable=True), sa.Column('audit', JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending','approved','rejected','running','applied','rolled_back','uncertain')", name='ck_task_change_sets_status'))
    op.create_index('ix_task_change_sets_task_id', 'task_change_sets', ['task_id'])
    op.create_table('owned_work_areas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='RESTRICT'), unique=True, nullable=False),
        sa.Column('bound_root', sa.Text(), nullable=False), sa.Column('root_identity', JSONB(), nullable=False),
        sa.Column('baseline', JSONB(), nullable=False), sa.Column('source_root', sa.Text(), nullable=False),
        sa.Column('source_revision', sa.BigInteger(), nullable=False), sa.Column('exported_id', sa.String(32), nullable=True))
    op.create_index('ix_owned_work_areas_source_task_id', 'owned_work_areas', ['source_task_id'])


def downgrade():
    connection = op.get_bind()
    connection.execute(sa.text('LOCK TABLE task_change_sets, owned_work_areas IN ACCESS EXCLUSIVE MODE'))
    if connection.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM task_change_sets) OR EXISTS(SELECT 1 FROM owned_work_areas)')):
        raise RuntimeError('refusing to discard change-set recovery or owned workspace history')
    op.drop_table('owned_work_areas')
    op.drop_table('task_change_sets')
