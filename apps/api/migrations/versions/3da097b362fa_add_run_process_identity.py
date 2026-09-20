"""Preserve execution identity on runs even when slot release has committed."""

from alembic import op
import sqlalchemy as sa

revision = '3da097b362fa'
down_revision = '2c9d86a251ef'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('owner_host_id', sa.String(64), nullable=True))
    op.add_column('agent_runs', sa.Column('owner_pid', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_runs', 'owner_pid')
    op.drop_column('agent_runs', 'owner_host_id')
