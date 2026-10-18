"""Merge payment and service commissions into one service commission."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _constraint_names(inspector: sa.Inspector, table: str) -> set[str]:
    return {
        name
        for item in inspector.get_check_constraints(table)
        if (name := item.get("name")) is not None
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    transaction_columns = {
        column["name"] for column in inspector.get_columns("payment_transactions")
    }
    if "payment_commission_percent" in transaction_columns:
        op.execute(
            sa.text(
                """
                UPDATE payment_transactions
                SET service_commission_percent =
                        service_commission_percent + payment_commission_percent,
                    service_commission_amount =
                        service_commission_amount + payment_commission_amount
                """
            )
        )
        constraints = _constraint_names(inspector, "payment_transactions")
        if "commission_non_negative" in constraints:
            op.drop_constraint("commission_non_negative", "payment_transactions", type_="check")
        op.drop_column("payment_transactions", "payment_commission_amount")
        op.drop_column("payment_transactions", "payment_commission_percent")
    constraints = _constraint_names(sa.inspect(bind), "payment_transactions")
    if "service_commission_range" not in constraints:
        op.create_check_constraint(
            "service_commission_range",
            "payment_transactions",
            "service_commission_percent BETWEEN 0 AND 100",
        )

    settings_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("commission_settings")
    }
    if "payment_percent" in settings_columns:
        op.execute(
            sa.text(
                """
                UPDATE commission_settings
                SET service_percent = service_percent + payment_percent
                """
            )
        )
        constraints = _constraint_names(sa.inspect(bind), "commission_settings")
        for name in (
            "commission_total",
            "payment_percent_non_negative",
            "service_percent_non_negative",
        ):
            if name in constraints:
                op.drop_constraint(name, "commission_settings", type_="check")
        op.drop_column("commission_settings", "payment_percent")
    constraints = _constraint_names(sa.inspect(bind), "commission_settings")
    if "service_percent_range" not in constraints:
        op.create_check_constraint(
            "service_percent_range",
            "commission_settings",
            "service_percent BETWEEN 0 AND 100",
        )


def downgrade() -> None:
    bind = op.get_bind()
    transaction_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("payment_transactions")
    }
    constraints = _constraint_names(sa.inspect(bind), "payment_transactions")
    if "service_commission_range" in constraints:
        op.drop_constraint("service_commission_range", "payment_transactions", type_="check")
    if "payment_commission_percent" not in transaction_columns:
        op.add_column(
            "payment_transactions",
            sa.Column(
                "payment_commission_percent",
                sa.Numeric(6, 3),
                nullable=False,
                server_default="0",
            ),
        )
        op.add_column(
            "payment_transactions",
            sa.Column(
                "payment_commission_amount",
                sa.Numeric(14, 2),
                nullable=False,
                server_default="0",
            ),
        )
        op.alter_column("payment_transactions", "payment_commission_percent", server_default=None)
        op.alter_column("payment_transactions", "payment_commission_amount", server_default=None)
    op.create_check_constraint(
        "commission_non_negative",
        "payment_transactions",
        "payment_commission_percent >= 0 AND service_commission_percent >= 0",
    )

    settings_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("commission_settings")
    }
    constraints = _constraint_names(sa.inspect(bind), "commission_settings")
    if "service_percent_range" in constraints:
        op.drop_constraint("service_percent_range", "commission_settings", type_="check")
    if "payment_percent" not in settings_columns:
        op.add_column(
            "commission_settings",
            sa.Column(
                "payment_percent",
                sa.Numeric(6, 3),
                nullable=False,
                server_default="0",
            ),
        )
        op.alter_column("commission_settings", "payment_percent", server_default=None)
    op.create_check_constraint(
        "payment_percent_non_negative",
        "commission_settings",
        "payment_percent >= 0",
    )
    op.create_check_constraint(
        "service_percent_non_negative",
        "commission_settings",
        "service_percent >= 0",
    )
    op.create_check_constraint(
        "commission_total",
        "commission_settings",
        "payment_percent + service_percent <= 100",
    )
