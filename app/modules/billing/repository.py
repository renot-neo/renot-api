"""Data access for the `billing` module.

Scope: plans, usage metering, and retention.

There's no SQLAlchemy `relationship()` between entities here (consistent
with the rest of this project - `bots`/`destinations`/`messaging` also use
plain FK columns + explicit queries) - `service.py` joins `OrganizationPlan`
+ `Plan` via two separate lookups when needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.billing.model import OrganizationPlan, Plan, UsageEvent, UsageEventType


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, plan_id: uuid.UUID) -> Plan | None:
        stmt = select(Plan).where(Plan.id == plan_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_default(self) -> Plan | None:
        stmt = select(Plan).where(Plan.is_default.is_(True))
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_all(self, *, params: PageParams) -> Page[Plan]:
        base = select(Plan)
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        # Ascending order (unlike `created_at.desc()` used by other listings
        # in this project) - the plan catalog is shown starting from the
        # first one seeded (Default), not newest-first.
        stmt = base.order_by(Plan.created_at.asc()).limit(params.limit).offset(params.offset)
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)


class OrganizationPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, *, tenant_id: uuid.UUID) -> OrganizationPlan | None:
        """Default lookup: excludes soft-deleted rows, scoped to the tenant."""
        stmt = select(OrganizationPlan).where(
            OrganizationPlan.tenant_id == tenant_id, OrganizationPlan.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, *, tenant_id: uuid.UUID, plan_id: uuid.UUID) -> OrganizationPlan:
        organization_plan = OrganizationPlan(tenant_id=tenant_id, plan_id=plan_id)
        self._session.add(organization_plan)
        await self._session.flush()
        return organization_plan

    async def set_plan(self, organization_plan: OrganizationPlan, *, plan_id: uuid.UUID) -> None:
        organization_plan.plan_id = plan_id
        await self._session.flush()

    async def list_active_with_retention(self) -> list[tuple[uuid.UUID, int]]:
        """`(tenant_id, retention_days)` for every active `OrganizationPlan`

        (`deleted_at IS NULL`) whose `Plan.retention_days` is set (`IS NOT
        NULL` - `None` means "keep forever", that org is deliberately
        excluded from this list). Used by the purge job
        (`service.get_purge_targets`, `tasks.purge_expired_usage_data`).
        """
        stmt = (
            select(OrganizationPlan.tenant_id, Plan.retention_days)
            .join(Plan, Plan.id == OrganizationPlan.plan_id)
            .where(OrganizationPlan.deleted_at.is_(None), Plan.retention_days.isnot(None))
        )
        result = await self._session.execute(stmt)
        return [(tenant_id, retention_days) for tenant_id, retention_days in result.all()]


class UsageEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        event_type: UsageEventType,
        bot_id: uuid.UUID,
        destination_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
        delivery_log_id: uuid.UUID | None = None,
    ) -> UsageEvent:
        event = UsageEvent(
            tenant_id=tenant_id,
            event_type=event_type,
            bot_id=bot_id,
            destination_id=destination_id,
            message_id=message_id,
            delivery_log_id=delivery_log_id,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def count_by_type_since(
        self, *, tenant_id: uuid.UUID, since: datetime
    ) -> dict[UsageEventType, int]:
        """Aggregate count per `event_type` since `since` - used by

        `GET /billing/usage` to compute the current period's usage (tenant-scoped).
        """
        stmt = (
            select(UsageEvent.event_type, func.count(UsageEvent.id))
            .where(UsageEvent.tenant_id == tenant_id, UsageEvent.created_at >= since)
            .group_by(UsageEvent.event_type)
        )
        result = await self._session.execute(stmt)
        return {event_type: count for event_type, count in result.all()}

    async def delete_before(
        self, *, tenant_id: uuid.UUID, before: datetime, batch_size: int
    ) -> int:
        """Hard-deletes ONE batch (at most `batch_size` rows) of this

        tenant's `UsageEvent`s with `created_at < before` (retention purge,
        see `service.purge_usage_events_batch`). Postgres doesn't support
        `DELETE ... LIMIT` directly - a subquery
        `WHERE id IN (SELECT id ... LIMIT batch_size)` is used instead. The
        caller (`billing/tasks.py`) loops + commits between batches (commit
        lives in the task, not the service/repository, consistent with the
        rest of this repo).
        """
        subquery = (
            select(UsageEvent.id)
            .where(UsageEvent.tenant_id == tenant_id, UsageEvent.created_at < before)
            .limit(batch_size)
        )
        stmt = delete(UsageEvent).where(UsageEvent.id.in_(subquery))
        result = await self._session.execute(stmt)
        return result.rowcount or 0  # type: ignore[attr-defined]
