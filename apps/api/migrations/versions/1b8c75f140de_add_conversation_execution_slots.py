"""Add exclusive execution slots for conversations."""

import sqlalchemy as sa
from alembic import op


revision = "1b8c75f140de"
down_revision = "0a7b64e039cd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 本课只增加存储结构，不根据历史 Run 状态推断执行是否仍然存活。
    # 建表及约束由 Alembic 的迁移事务统一管理。
    op.create_table(
        "conversation_execution_slots",
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "owner_token",
            sa.String(32),
            nullable=False,
        ),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "owner_token ~ '^[0-9a-f]{32}$'",
            name="ck_conversation_execution_slots_owner_token",
        ),
    )

    # 主键已经建立 conversation_id 的唯一索引，无需重复创建普通索引。


def downgrade() -> None:
    # 回退会丢失全部占用记录，不能在仍有执行任务时用来解除占用。
    # 回退验证只在隔离测试库进行。
    op.drop_table("conversation_execution_slots")
