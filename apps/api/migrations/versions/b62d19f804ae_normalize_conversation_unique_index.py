"""Normalize legacy conversation uniqueness to the original migration contract.

Online migration: inspect existing objects so fresh databases remain unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "b62d19f804ae"
down_revision = "a91c42e7d603"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("This repair requires an online PostgreSQL connection")
    inspector = sa.inspect(connection)
    indexes = {item["name"]: item for item in inspector.get_indexes("conversations")}
    constraints = {
        item["name"]: item for item in inspector.get_unique_constraints("conversations")
    }
    index = indexes.get("ix_conversations_external_id")
    legacy = constraints.get("uq_conversations_external_id")
    if legacy and legacy["column_names"] != ["external_id"]:
        raise RuntimeError("Unexpected legacy constraint columns; refusing repair")
    if index:
        options = index.get("dialect_options", {})
        if (
            not index["unique"]
            or index["column_names"] != ["external_id"]
            or index.get("duplicates_constraint")
            or options.get("postgresql_where")
            or options.get("postgresql_include")
            or options.get("postgresql_nulls_not_distinct")
        ):
            raise RuntimeError("Unexpected target index definition; refusing repair")
        valid = connection.scalar(
            sa.text(
                "SELECT indisvalid AND indisready FROM pg_index "
                "WHERE indexrelid = to_regclass('ix_conversations_external_id')"
            )
        )
        if valid is not True:
            raise RuntimeError("Target index is not valid and ready; refusing repair")
    else:
        op.create_index(
            "ix_conversations_external_id",
            "conversations",
            ["external_id"],
            unique=True,
        )
    if legacy:
        # The new unique index is already enforcing uniqueness at this point.
        op.drop_constraint(
            "uq_conversations_external_id", "conversations", type_="unique"
        )


def downgrade() -> None:
    # Intentional no-op: the previous revision's declared schema already required
    # this unique index. Do not reintroduce drift or remove uniqueness on rollback.
    pass
