"""Persist explicit project write grants independently of proposal approval."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b5a6e4f9c32d'
down_revision = 'a4f5d3e8b21c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 不从历史approved记录推导许可；只有显式内部发放才创建记录。
    op.create_table(
        'project_write_grants',
        sa.Column('grant_id', sa.String(32), primary_key=True),
        sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('file_edit_proposals.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('target', postgresql.JSONB(), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.CheckConstraint("grant_id ~ '^[0-9a-f]{32}$'", name='ck_project_write_grants_id'),
        sa.CheckConstraint('(enabled AND revision = 1) OR (NOT enabled AND revision = 2)', name='ck_project_write_grants_state'),
        sa.CheckConstraint("jsonb_typeof(target) = 'object'", name='ck_project_write_grants_target'),
    )


def downgrade() -> None:
    connection = op.get_bind()
    # 防止回退丢失已发放/撤销事实，再升级后绕过一次发放约束。
    connection.execute(sa.text('LOCK TABLE project_write_grants IN ACCESS EXCLUSIVE MODE'))
    if connection.scalar(sa.text('SELECT EXISTS (SELECT 1 FROM project_write_grants)')):
        raise RuntimeError('存在项目写入许可记录，不能无损回退')
    op.drop_table('project_write_grants')
