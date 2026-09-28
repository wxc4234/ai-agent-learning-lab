"""Persist workspace binding revisions without granting project write access."""

from alembic import op
import sqlalchemy as sa

revision = 'a4f5d3e8b21c'
down_revision = '93e4c2d7a10b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧绑定从共同基线1开始，不虚构此前的修订历史；保持路径与归属不变。
    op.add_column('workspaces', sa.Column(
        'binding_revision', sa.BigInteger(), nullable=False, server_default='1',
    ))
    op.create_check_constraint(
        'ck_workspaces_binding_revision_positive', 'workspaces', 'binding_revision > 0',
    )


def downgrade() -> None:
    connection = op.get_bind()
    # 与绑定更新互斥；已有版本进展不能通过降级/再升级重置为1。
    connection.execute(sa.text('LOCK TABLE workspaces IN ACCESS EXCLUSIVE MODE'))
    if connection.scalar(sa.text('SELECT EXISTS (SELECT 1 FROM workspaces WHERE binding_revision <> 1)')):
        raise RuntimeError('存在目录绑定修订，不能无损回退')
    op.drop_constraint('ck_workspaces_binding_revision_positive', 'workspaces', type_='check')
    op.drop_column('workspaces', 'binding_revision')
