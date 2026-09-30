"""Store controlled code embedding batches with model-space constraints.

Revision ID: 6f4c2b8d901a
Revises: d7c8a61be54f
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import VECTOR

revision = "6f4c2b8d901a"
down_revision = "d7c8a61be54f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 扩展是数据库共享对象；仅显式迁移安装，固定public供私有schema引用。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
                WHERE e.extname='vector' AND n.nspname <> 'public'
            ) THEN
                RAISE EXCEPTION 'vector extension must be installed in public';
            END IF;
        END $$
    """)
    op.create_table(
        "code_embedding_spaces",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("requested_model", sa.String(256), nullable=False),
        sa.Column("response_model", sa.String(256), nullable=False),
        sa.UniqueConstraint(
            "id", "dimensions", name="uq_code_embedding_spaces_dimensions"
        ),
        sa.CheckConstraint("id ~ '^[a-f0-9]{64}$'", name="ck_code_embedding_spaces_id"),
        sa.CheckConstraint(
            "dimensions BETWEEN 1 AND 4096", name="ck_code_embedding_spaces_dimensions"
        ),
        sa.CheckConstraint(
            "char_length(requested_model) BETWEEN 1 AND 256 AND char_length(response_model) BETWEEN 1 AND 256",
            name="ck_code_embedding_spaces_models",
        ),
    )
    op.create_table(
        "code_embedding_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("space_id", sa.String(64), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("bound_root", sa.Text(), nullable=False),
        sa.Column("binding_revision", sa.BigInteger(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("source_metadata", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "dimensions"],
            ["code_embedding_spaces.id", "code_embedding_spaces.dimensions"],
            name="fk_code_embedding_batches_space",
        ),
        sa.UniqueConstraint(
            "id", "space_id", "dimensions", name="uq_code_embedding_batches_space"
        ),
        sa.CheckConstraint(
            "binding_revision > 0 AND char_length(bound_root) > 0",
            name="ck_code_embedding_batches_binding",
        ),
        sa.CheckConstraint(
            "chunk_count BETWEEN 1 AND 20", name="ck_code_embedding_batches_count"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="ck_code_embedding_batches_metadata",
        ),
    )
    op.create_index(
        "ix_code_embedding_batches_task_id", "code_embedding_batches", ["task_id"]
    )
    op.create_table(
        "code_embedding_vectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("space_id", sa.String(64), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.String(64), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_metadata", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id", "space_id", "dimensions"],
            [
                "code_embedding_batches.id",
                "code_embedding_batches.space_id",
                "code_embedding_batches.dimensions",
            ],
            ondelete="CASCADE",
            name="fk_code_embedding_vectors_batch",
        ),
        sa.UniqueConstraint(
            "batch_id", "ordinal", name="uq_code_embedding_vectors_ordinal"
        ),
        sa.UniqueConstraint(
            "batch_id", "chunk_id", name="uq_code_embedding_vectors_chunk"
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 0 AND 19", name="ck_code_embedding_vectors_ordinal"
        ),
        sa.CheckConstraint(
            "dimensions BETWEEN 1 AND 4096 AND vector_dims(embedding) = dimensions",
            name="ck_code_embedding_vectors_dimensions",
        ),
        sa.CheckConstraint(
            "chunk_id ~ '^[a-f0-9]{64}$'", name="ck_code_embedding_vectors_id"
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 2000 AND octet_length(content) <= 4096",
            name="ck_code_embedding_vectors_content",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(chunk_metadata) = 'object'",
            name="ck_code_embedding_vectors_metadata",
        ),
    )


def downgrade() -> None:
    # 先锁表再判断，避免检查后并发插入使降级静默丢失快照。
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE code_embedding_spaces, code_embedding_batches, code_embedding_vectors "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM code_embedding_spaces) "
            "OR EXISTS(SELECT 1 FROM code_embedding_batches) "
            "OR EXISTS(SELECT 1 FROM code_embedding_vectors)"
        )
    ):
        raise RuntimeError("refusing to discard code embedding snapshots")
    op.drop_table("code_embedding_vectors")
    op.drop_table("code_embedding_batches")
    op.drop_table("code_embedding_spaces")
    # 不DROP EXTENSION：它可能被其他schema/应用使用。
