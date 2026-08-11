"""Business logic for the `billing` module.

Scope: plans, usage metering, and retention.

This is the layer the router calls into - the only place business logic
belongs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.billing.exceptions import DefaultPlanNotConfiguredError, PlanNotFoundError
from app.modules.billing.model import Plan, UsageEventType
from app.modules.billing.repository import (
    OrganizationPlanRepository,
    PlanRepository,
    UsageEventRepository,
)


@dataclass
class UsageSummary:
    plan: Plan
    period_start: datetime
    period_end: datetime
    event_in_count: int
    event_out_count: int
    total_event_count: int
    quota_used_percent: float | None
    over_quota: bool


async def _ensure_organization_plan(session: AsyncSession, *, tenant_id: uuid.UUID) -> Plan:
    """Returns this org's active `Plan`, auto-assigning the default plan if

    the org doesn't yet have an `OrganizationPlan` row (assign-on-read, NOT
    a data migration backfill) - covers both cases without an extra
    migration: new orgs and older orgs created before the `billing` module existed.
    """
    org_plan_repo = OrganizationPlanRepository(session)
    plan_repo = PlanRepository(session)

    organization_plan = await org_plan_repo.get_active(tenant_id=tenant_id)
    if organization_plan is not None:
        plan = await plan_repo.get_by_id(organization_plan.plan_id)
        if plan is not None:
            return plan
        # The assigned plan no longer exists in the DB (should never happen
        # - there's no Plan-delete endpoint in this MVP) - fall through to
        # the default path below so the org still ends up with a valid plan.

    default_plan = await plan_repo.get_default()
    if default_plan is None:
        raise DefaultPlanNotConfiguredError()

    if organization_plan is None:
        await org_plan_repo.create(tenant_id=tenant_id, plan_id=default_plan.id)
    else:
        await org_plan_repo.set_plan(organization_plan, plan_id=default_plan.id)
    return default_plan


async def list_plans(session: AsyncSession, *, page_params: PageParams) -> Page[Plan]:
    return await PlanRepository(session).list_all(params=page_params)


async def subscribe_plan(
    session: AsyncSession, *, tenant_id: uuid.UUID, plan_id: uuid.UUID
) -> Plan:
    """`POST /billing/subscribe-plan` - assigns a plan to the active org.

    There's NO payment gateway integration in this MVP, so this is purely a
    plan-assignment change, not an actual checkout/payment.
    """
    plan = await PlanRepository(session).get_by_id(plan_id)
    if plan is None:
        raise PlanNotFoundError()

    org_plan_repo = OrganizationPlanRepository(session)
    organization_plan = await org_plan_repo.get_active(tenant_id=tenant_id)
    if organization_plan is None:
        await org_plan_repo.create(tenant_id=tenant_id, plan_id=plan.id)
    else:
        await org_plan_repo.set_plan(organization_plan, plan_id=plan.id)
    return plan


def _current_utc_month_bounds(now: datetime) -> tuple[datetime, datetime]:
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1)
    else:
        period_end = period_start.replace(month=period_start.month + 1)
    return period_start, period_end


async def get_usage(session: AsyncSession, *, tenant_id: uuid.UUID) -> UsageSummary:
    """`GET /billing/usage` - usage for the current calendar month (UTC),

    warn-only (see the `over_quota` design notes in `schema.py`).
    """
    plan = await _ensure_organization_plan(session, tenant_id=tenant_id)
    period_start, period_end = _current_utc_month_bounds(datetime.now(UTC))

    counts = await UsageEventRepository(session).count_by_type_since(
        tenant_id=tenant_id, since=period_start
    )
    event_in_count = counts.get(UsageEventType.IN, 0)
    event_out_count = counts.get(UsageEventType.OUT, 0)
    total_event_count = event_in_count + event_out_count

    quota_used_percent: float | None = None
    over_quota = False
    if plan.monthly_event_quota is not None and plan.monthly_event_quota > 0:
        quota_used_percent = round(total_event_count / plan.monthly_event_quota * 100, 2)
        over_quota = total_event_count > plan.monthly_event_quota

    return UsageSummary(
        plan=plan,
        period_start=period_start,
        period_end=period_end,
        event_in_count=event_in_count,
        event_out_count=event_out_count,
        total_event_count=total_event_count,
        quota_used_percent=quota_used_percent,
        over_quota=over_quota,
    )


async def create_usage_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: UsageEventType,
    bot_id: uuid.UUID,
    destination_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    delivery_log_id: uuid.UUID | None = None,
) -> None:
    """Called by `tasks.record_usage_event` (Celery, queue `billing.metering`)

    - a plain DB write, with no extra validation/business rule (warn-only,
    no quota enforcement at this point, see the design notes above).
    """
    await UsageEventRepository(session).create(
        tenant_id=tenant_id,
        event_type=event_type,
        bot_id=bot_id,
        destination_id=destination_id,
        message_id=message_id,
        delivery_log_id=delivery_log_id,
    )


def enqueue_event_in(*, tenant_id: uuid.UUID, bot_id: uuid.UUID) -> None:
    """An event_in is recorded for EVERY inbound update that passes secret

    token validation (not just recognized commands - any update received
    from Telegram counts). Called by `modules/webhooks.router` AFTER
    `session.commit()` (the router->DB->Celery pattern, same as
    `modules/messaging`) so the Celery worker (a separate process/DB
    connection, `WorkerAsyncSessionFactory`) doesn't race to read a row
    that hasn't been committed yet. Queue `billing.metering` - eventual/low
    priority, must NEVER delay the webhook reply to Telegram (unlike
    `messaging.send`, which needs to be fast).

    `tasks` is imported locally (not at top level) to avoid a cycle with
    `tasks.py`, which imports this module for `create_usage_event` - same
    pattern as `modules/auth.service` <-> `modules/organizations.service`
    (see the comment there).
    """
    from app.modules.billing.tasks import record_usage_event

    record_usage_event.delay(
        tenant_id=str(tenant_id), event_type=UsageEventType.IN.value, bot_id=str(bot_id)
    )


def enqueue_event_out(
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    destination_id: uuid.UUID,
    message_id: uuid.UUID,
    delivery_log_id: uuid.UUID,
) -> None:
    """An event_out is recorded AFTER a message is successfully sent to

    Telegram (not when the `DeliveryLog` is created as `queued`). Called by
    `modules/messaging.tasks._process_delivery` AFTER its own `mark_sent` +
    commit - see the `enqueue_event_in` docstring for why the ordering/local
    import matter.
    """
    from app.modules.billing.tasks import record_usage_event

    record_usage_event.delay(
        tenant_id=str(tenant_id),
        event_type=UsageEventType.OUT.value,
        bot_id=str(bot_id),
        destination_id=str(destination_id),
        message_id=str(message_id),
        delivery_log_id=str(delivery_log_id),
    )


async def get_purge_targets(session: AsyncSession) -> list[tuple[uuid.UUID, int]]:
    """`(tenant_id, retention_days)` for every org with an active `Plan`

    whose `retention_days` is set (`None` = keep forever, that org isn't
    included in the list - see the `Plan.retention_days` docstring in
    `model.py`). Called by `tasks.purge_expired_usage_data` (a daily Celery
    beat job, queue `billing.metering`).
    """
    return await OrganizationPlanRepository(session).list_active_with_retention()


async def purge_usage_events_batch(
    session: AsyncSession, *, tenant_id: uuid.UUID, before: datetime, batch_size: int
) -> int:
    """Hard-deletes ONE batch (at most `batch_size` rows) of this tenant's

    `UsageEvent`s with `created_at < before`. Batched (rather than one huge
    `DELETE`) so this high-volume table isn't locked for long - the caller
    (`tasks.py`) loops this call until it returns `0`, committing between
    batches, same pattern as `purge_delivery_logs_batch` (`modules/messaging`).
    """
    return await UsageEventRepository(session).delete_before(
        tenant_id=tenant_id, before=before, batch_size=batch_size
    )
