"""Record local execution process identity without guessing legacy owners."""

from alembic import op
import sqlalchemy as sa

revision = '2c9d86a251ef'
down_revision = '1b8c75f140de'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧占用无身份信息，保留 NULL 并拒绝自动恢复。
    op.add_column('conversation_execution_slots', sa.Column('owner_host_id', sa.String(64), nullable=True))
    op.add_column('conversation_execution_slots', sa.Column('owner_pid', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversation_execution_slots', 'owner_pid')
    op.drop_column('conversation_execution_slots', 'owner_host_id')
