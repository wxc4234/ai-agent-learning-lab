"""Add server-side login sessions without altering existing user or chat data."""

import sqlalchemy as sa
from alembic import op

revision = "c83f20a915bd"
down_revision = "b62d19f804ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_login_sessions_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_login_sessions_expiration"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_login_sessions_revocation",
        ),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_index("ix_login_sessions_token_hash", "login_sessions", ["token_hash"], unique=True)
    op.create_index("ix_login_sessions_expires_at", "login_sessions", ["expires_at"])


def downgrade() -> None:
    # This discards login sessions; rehearse only in an isolated test database.
    op.drop_index("ix_login_sessions_expires_at", table_name="login_sessions")
    op.drop_index("ix_login_sessions_token_hash", table_name="login_sessions")
    op.drop_index("ix_login_sessions_user_id", table_name="login_sessions")
    op.drop_table("login_sessions")
