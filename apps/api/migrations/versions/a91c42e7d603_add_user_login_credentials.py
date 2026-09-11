"""Add optional paired login credentials without changing existing identities."""

import sqlalchemy as sa
from alembic import op

revision = "a91c42e7d603"
down_revision = "fed4e53cb0f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("username", sa.String(64), nullable=True))
        batch.add_column(sa.Column("password_hash", sa.Text(), nullable=True))
        batch.create_index("ix_users_username", ["username"], unique=True)
        batch.create_check_constraint(
            "ck_users_login_credentials_pair",
            "(username IS NULL AND password_hash IS NULL) OR "
            "(username IS NOT NULL AND password_hash IS NOT NULL)",
        )


def downgrade() -> None:
    # Dropping these columns discards credentials; only rehearse on disposable data.
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_login_credentials_pair", type_="check")
        batch.drop_index("ix_users_username")
        batch.drop_column("password_hash")
        batch.drop_column("username")
