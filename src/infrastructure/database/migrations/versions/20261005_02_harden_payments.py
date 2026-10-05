"""Add provider idempotency and payment constraints.

Revision ID: 20261005_02
Revises: 20261005_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20261005_02"
down_revision = "20261005_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("provider_payment_id", sa.String(255), nullable=True),
    )
    op.create_check_constraint(
        "ck_payments_amount_positive",
        "payments",
        "amount > 0",
    )
    op.create_check_constraint(
        "ck_payments_provider_for_operation",
        "payments",
        "provider_payment_id IS NULL OR payment_provider IS NOT NULL",
    )
    op.create_unique_constraint(
        "uq_payments_provider_operation",
        "payments",
        ["payment_provider", "provider_payment_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_payments_provider_operation",
        "payments",
        type_="unique",
    )
    op.drop_constraint(
        "ck_payments_amount_positive",
        "payments",
        type_="check",
    )
    op.drop_constraint(
        "ck_payments_provider_for_operation",
        "payments",
        type_="check",
    )
    op.drop_column("payments", "provider_payment_id")
