"""SQLAlchemy models for the `billing` module.

Scope: plans, usage metering, and retention.

Three entities:
- `Plan`: **non-tenant** (plain `Base`, like `users`) - the platform's plan
  catalog (`monthly_event_quota`/`price`), the same for every tenant. There's
  no Plan CRUD endpoint in this MVP (only `GET /billing/plans`) - Plan rows
  are populated purely via data migration (`alembic/versions/`), with no
  admin panel yet to manage tiers. See the `is_default` note below.
- `OrganizationPlan`: tenant-scoped (`TenantScopedBase`) - each org's active
  plan assignment. One org = exactly one active row (partial unique index on
  `tenant_id` `WHERE deleted_at IS NULL`). Switching plans means UPDATEing
  `plan_id` on the same row (not soft-deleting the old one + creating a new
  one) - this MVP doesn't need a plan-change history; `deleted_at` is
  inherited from `TenantScopedBase` for consistency but in practice is never
  set, since there's no "unsubscribe-from-all-plans" endpoint in scope.
- `UsageEvent`: **NOT** `TenantScopedBase` (no `deleted_at`) - this is
  high-volume operational data subject to a retention policy (deleted via a
  scheduled job), identical pattern to `modules/messaging.DeliveryLog`.
  `created_at` doubles as the purge key - the actual purge job is
  `Plan.retention_days` + `tasks.purge_expired_usage_data` (a daily Celery
  beat job), see the docstrings below/in `tasks.py`.

Design notes:
- **Plan tiers**: pricing tiers/payment gateway integration are deliberately
  out of scope for this MVP. It seeds a SINGLE `Plan` ("Default") with
  `monthly_event_quota=None` (no practical limit) so a new org has an active
  plan immediately without having to choose one - paid tiers + pricing come
  later once payment gateway integration is built. `is_default` marks this
  plan so `service._ensure_organization_plan` knows which plan to
  auto-assign (both for new orgs and for older orgs created before the
  `billing` module existed, see its docstring - assign-on-read, not a data
  migration backfill).
- **Over-quota policy**: whether to hard-stop or overage-charge once a quota
  is exceeded is deliberately undecided for now. This MVP is **warn-only** -
  metering (`UsageEvent`) keeps running fully, with NO enforcement that
  blocks `POST /messages` or inbound webhooks even past quota;
  `GET /billing/usage` simply surfaces `over_quota` so the org is aware.
  Overage-charge infrastructure (e.g. an `is_overage` column per event) is
  deferred until payment gateway integration exists.
- **Retention purge**: `Plan.retention_days` (nullable, `None` = keep
  forever) - chosen as a per-`Plan` field (not a global constant) so a
  higher plan tier can mean longer retention, without needing another
  migration once paid plan tiers are designed. This MVP's "Default" plan is
  seeded with `retention_days=30` (see the `add_plan_retention_days`
  migration). The purge job (`tasks.purge_expired_usage_data`, a daily
  Celery beat job) deletes `UsageEvent` AND `messaging.DeliveryLog` rows
  whose `created_at` is older than the org's `retention_days` (two tables,
  same period - see `service.py`).
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TenantScopedBase, TimestampMixin, UUIDPrimaryKeyMixin


class UsageEventType(enum.StrEnum):
    """`event_in`/`event_out`."""

    IN = "in"
    OUT = "out"


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # `None` = no practical limit (this MVP's "Default" plan, see the module
    # docstring). Future paid plans will set a finite number here.
    monthly_event_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # `None` = not yet priced (payment gateway integration doesn't exist
    # yet). Not `0`, so "free" vs. "pricing not decided yet" isn't ambiguous.
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # The plan auto-assigned to an org that doesn't yet have an
    # `OrganizationPlan` (see `service._ensure_organization_plan`). Only ONE
    # row is allowed to be `True` - enforced at the data-migration/service
    # level, not a DB constraint (there's no endpoint that can create a new
    # `Plan` in this MVP that could violate this invariant).
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # `None` = keep `UsageEvent`/`DeliveryLog` forever (no purge for this
    # plan). Read by `tasks.purge_expired_usage_data` via
    # `OrganizationPlanRepository.list_active_with_retention` - see the
    # module docstring above & `service.py`.
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OrganizationPlan(TenantScopedBase):
    __tablename__ = "organization_plans"
    __table_args__ = (
        Index(
            "uq_organization_plans_tenant_id_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    # FK to `plans.id` (a non-tenant table in this same module, so a direct
    # model class import is safe here - unlike the other cross-module FKs in
    # this file, which deliberately use a table name string instead).
    plan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plans.id"), index=True, nullable=False
    )


class UsageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hard-delete only (see the module docstring) - one row per event_in/

    event_out (one outbound message successfully delivered to one
    destination, or one inbound update received from Telegram/an external app).
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_tenant_id_created_at", "tenant_id", "created_at"),
        Index(
            "ix_usage_events_tenant_id_event_type_created_at",
            "tenant_id",
            "event_type",
            "created_at",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False
    )
    event_type: Mapped[UsageEventType] = mapped_column(
        Enum(UsageEventType, name="usage_event_type"), nullable=False
    )
    # FK by table name string to `bots`/`destinations`/`messages`/
    # `delivery_logs` (this module doesn't import other modules' model
    # classes). `bot_id` is always set (both event_in and event_out always
    # have a bot); the other three are only set for event_out - `None` for event_in.
    bot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bots.id"), index=True, nullable=False
    )
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id"), nullable=True
    )
    delivery_log_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("delivery_logs.id"), nullable=True
    )
