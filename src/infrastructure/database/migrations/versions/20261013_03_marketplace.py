"""Marketplace roles, tariffs, accounting and message links.

Revision ID: 20261013_03
Revises: 20261005_02
"""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "20261013_03"
down_revision = "20261005_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experts", sa.Column("specialization", sa.String(255)))
    op.add_column("experts", sa.Column("education", sa.String(1000)))
    op.add_column("experts", sa.Column("experience", sa.String(1000)))
    op.add_column("experts", sa.Column("photo_file_id", sa.String(255)))

    op.create_table(
        "tariffs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("message_limit", sa.Integer()),
        sa.Column("base_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("duration_days > 0", name="ck_tariff_duration"),
        sa.CheckConstraint("base_price > 0", name="ck_tariff_price"),
        sa.CheckConstraint("message_limit IS NULL OR message_limit > 0", name="ck_tariff_limit"),
    )
    op.create_table(
        "expert_prices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("expert_id", sa.Uuid(), sa.ForeignKey("experts.id", ondelete="CASCADE")),
        sa.Column("tariff_id", sa.Uuid(), sa.ForeignKey("tariffs.id", ondelete="CASCADE")),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint("price > 0", name="ck_expert_price_positive"),
        sa.UniqueConstraint("expert_id", "tariff_id", name="uq_expert_tariff_price"),
    )
    op.create_index("ix_expert_prices_expert_id", "expert_prices", ["expert_id"])
    op.create_table(
        "expert_applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("specialization", sa.String(255), nullable=False),
        sa.Column("education", sa.String(1000), nullable=False),
        sa.Column("experience", sa.String(1000), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("photo_file_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_expert_applications_user_id", "expert_applications", ["user_id"])
    op.create_index("ix_expert_applications_status", "expert_applications", ["status"])
    op.create_index(
        "uq_expert_applications_pending_user", "expert_applications", ["user_id"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "fee_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("service_percent", sa.Numeric(5, 2), nullable=False),
        sa.CheckConstraint(
            "payment_percent >= 0 AND service_percent >= 0 AND "
            "payment_percent + service_percent <= 100",
            name="ck_fee_percentages",
        ),
    )
    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("expert_id", sa.Uuid(), sa.ForeignKey("experts.id", ondelete="CASCADE")),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("bank_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("processed_by", sa.BigInteger()),
        sa.CheckConstraint("amount > 0", name="ck_withdrawal_amount"),
    )
    op.create_index("ix_withdrawals_expert_id", "withdrawals", ["expert_id"])
    op.create_index("ix_withdrawals_status", "withdrawals", ["status"])

    op.add_column("subscriptions", sa.Column("tariff_id", sa.Uuid()))
    op.add_column("subscriptions", sa.Column("message_limit", sa.Integer()))
    op.add_column("subscriptions", sa.Column("messages_sent", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key("fk_sub_tariff", "subscriptions", "tariffs", ["tariff_id"], ["id"], ondelete="SET NULL")
    op.create_check_constraint("ck_sub_message_limit", "subscriptions", "message_limit IS NULL OR message_limit > 0")
    op.create_check_constraint("ck_sub_messages_sent", "subscriptions", "messages_sent >= 0")
    op.add_column("messages", sa.Column("reply_to_id", sa.Uuid()))
    op.create_foreign_key("fk_message_reply", "messages", "messages", ["reply_to_id"], ["id"], ondelete="SET NULL")

    payment_columns: list[sa.Column[Any]] = [
        sa.Column("expert_id", sa.Uuid()),
        sa.Column("tariff_id", sa.Uuid()),
        sa.Column("tariff_name", sa.String(255)),
        sa.Column("duration_days", sa.Integer()),
        sa.Column("message_limit", sa.Integer()),
        sa.Column("payment_url", sa.String(2048)),
        sa.Column("payment_fee_percent", sa.Numeric(5, 2)),
        sa.Column("service_fee_percent", sa.Numeric(5, 2)),
        sa.Column("payment_fee", sa.Numeric(12, 2)),
        sa.Column("service_fee", sa.Numeric(12, 2)),
        sa.Column("expert_income", sa.Numeric(12, 2)),
    ]
    for column in payment_columns:
        op.add_column("payments", column)
    op.create_foreign_key("fk_payment_expert", "payments", "experts", ["expert_id"], ["id"])
    op.create_foreign_key("fk_payment_tariff", "payments", "tariffs", ["tariff_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_payments_expert_id", "payments", ["expert_id"])

    tariffs = sa.table(
        "tariffs",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("duration_days", sa.Integer()),
        sa.column("message_limit", sa.Integer()),
        sa.column("base_price", sa.Numeric()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(tariffs, [{
        "id": UUID("2cb293ca-11b6-48e6-8a24-c08e0685c11a"),
        "name": "Недельный",
        "description": "Доступ к эксперту на 7 дней",
        "duration_days": 7,
        "message_limit": None,
        "base_price": 1000,
        "is_active": True,
    }])
    fees = sa.table("fee_settings", sa.column("id", sa.Integer()), sa.column("payment_percent", sa.Numeric()), sa.column("service_percent", sa.Numeric()))
    op.bulk_insert(fees, [{"id": 1, "payment_percent": 0, "service_percent": 0}])


def downgrade() -> None:
    raise RuntimeError("Откат этой миграции удалит данные; выполните его вручную после резервной копии.")
