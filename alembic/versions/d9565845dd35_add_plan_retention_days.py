"""add_plan_retention_days

Revision ID: d9565845dd35
Revises: 282eb43ad43c
Create Date: 2026-08-10 21:12:30.698783

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9565845dd35"
down_revision: str | None = "282eb43ad43c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# See the `app/modules/billing/model.py` docstring for `Plan.retention_days`:
# the "Default" plan (same id as `_DEFAULT_PLAN_ID` in the
# `282eb43ad43c_create_billing_tables` migration) gets a 30-day retention
# for `UsageEvent`/`messaging.DeliveryLog`. Matched by `is_default` (rather
# than hardcoding its UUID again) - enough for this MVP since only one Plan
# can ever be `is_default=True` (enforced at the service level, see model.py).
_plans_table = sa.table(
    "plans",
    sa.column("id", sa.UUID()),
    sa.column("is_default", sa.Boolean()),
    sa.column("retention_days", sa.Integer()),
)
_DEFAULT_PLAN_RETENTION_DAYS = 30


def upgrade() -> None:
    op.add_column("plans", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.execute(
        _plans_table.update()
        .where(_plans_table.c.is_default.is_(True))
        .values(retention_days=_DEFAULT_PLAN_RETENTION_DAYS)
    )


def downgrade() -> None:
    op.drop_column("plans", "retention_days")
