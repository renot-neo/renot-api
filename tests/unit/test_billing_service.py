"""Unit tests for `app.modules.billing.service`.

Pure logic - the repository is mocked, no real DB. `enqueue_event_in`/
`enqueue_event_out` are tested by patching
`app.modules.billing.tasks.record_usage_event` (the Celery task object)
so `.delay()` never actually sends to the Redis broker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.pagination import Page, PageParams
from app.modules.billing import service
from app.modules.billing.exceptions import DefaultPlanNotConfiguredError, PlanNotFoundError
from app.modules.billing.model import OrganizationPlan, Plan, UsageEventType


def _plan(**overrides: object) -> Plan:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "Default",
        "monthly_event_quota": None,
        "price": None,
        "is_default": True,
    }
    defaults.update(overrides)
    return Plan(**defaults)  # type: ignore[arg-type]


def _organization_plan(**overrides: object) -> OrganizationPlan:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "plan_id": uuid.uuid4(),
    }
    defaults.update(overrides)
    return OrganizationPlan(**defaults)  # type: ignore[arg-type]


# --- _ensure_organization_plan ---


@pytest.mark.asyncio
async def test_ensure_organization_plan_returns_existing_plan() -> None:
    tenant_id = uuid.uuid4()
    plan = _plan()
    organization_plan = _organization_plan(tenant_id=tenant_id, plan_id=plan.id)

    with (
        patch("app.modules.billing.service.OrganizationPlanRepository") as org_plan_repo_cls,
        patch("app.modules.billing.service.PlanRepository") as plan_repo_cls,
    ):
        org_plan_repo_cls.return_value.get_active = AsyncMock(return_value=organization_plan)
        plan_repo_cls.return_value.get_by_id = AsyncMock(return_value=plan)

        result = await service._ensure_organization_plan(AsyncMock(), tenant_id=tenant_id)

        assert result is plan
        plan_repo_cls.return_value.get_by_id.assert_awaited_once_with(plan.id)


@pytest.mark.asyncio
async def test_ensure_organization_plan_auto_assigns_default_when_missing() -> None:
    tenant_id = uuid.uuid4()
    default_plan = _plan(is_default=True)

    with (
        patch("app.modules.billing.service.OrganizationPlanRepository") as org_plan_repo_cls,
        patch("app.modules.billing.service.PlanRepository") as plan_repo_cls,
    ):
        org_plan_repo = org_plan_repo_cls.return_value
        org_plan_repo.get_active = AsyncMock(return_value=None)
        org_plan_repo.create = AsyncMock()
        plan_repo_cls.return_value.get_default = AsyncMock(return_value=default_plan)

        result = await service._ensure_organization_plan(AsyncMock(), tenant_id=tenant_id)

        assert result is default_plan
        org_plan_repo.create.assert_awaited_once_with(tenant_id=tenant_id, plan_id=default_plan.id)


@pytest.mark.asyncio
async def test_ensure_organization_plan_raises_when_no_default_configured() -> None:
    with (
        patch("app.modules.billing.service.OrganizationPlanRepository") as org_plan_repo_cls,
        patch("app.modules.billing.service.PlanRepository") as plan_repo_cls,
    ):
        org_plan_repo_cls.return_value.get_active = AsyncMock(return_value=None)
        plan_repo_cls.return_value.get_default = AsyncMock(return_value=None)

        with pytest.raises(DefaultPlanNotConfiguredError):
            await service._ensure_organization_plan(AsyncMock(), tenant_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_ensure_organization_plan_falls_back_to_default_when_assigned_plan_gone() -> None:
    """`OrganizationPlan.plan_id` no longer references an existing `Plan` -

    falls back to the default plan & fixes the assignment (instead of
    raising), see the `_ensure_organization_plan` docstring.
    """
    tenant_id = uuid.uuid4()
    organization_plan = _organization_plan(tenant_id=tenant_id)
    default_plan = _plan(is_default=True)

    with (
        patch("app.modules.billing.service.OrganizationPlanRepository") as org_plan_repo_cls,
        patch("app.modules.billing.service.PlanRepository") as plan_repo_cls,
    ):
        org_plan_repo = org_plan_repo_cls.return_value
        org_plan_repo.get_active = AsyncMock(return_value=organization_plan)
        org_plan_repo.set_plan = AsyncMock()
        plan_repo = plan_repo_cls.return_value
        plan_repo.get_by_id = AsyncMock(return_value=None)
        plan_repo.get_default = AsyncMock(return_value=default_plan)

        result = await service._ensure_organization_plan(AsyncMock(), tenant_id=tenant_id)

        assert result is default_plan
        org_plan_repo.set_plan.assert_awaited_once_with(organization_plan, plan_id=default_plan.id)


# --- list_plans ---


@pytest.mark.asyncio
async def test_list_plans_delegates_to_repository() -> None:
    plans = [_plan(), _plan()]
    params = PageParams()
    with patch("app.modules.billing.service.PlanRepository") as repo_cls:
        repo_cls.return_value.list_all = AsyncMock(
            return_value=Page(items=plans, total=2, page=1, page_size=params.page_size)
        )

        result = await service.list_plans(AsyncMock(), page_params=params)

        assert result.items == plans
        repo_cls.return_value.list_all.assert_awaited_once_with(params=params)


# --- subscribe_plan ---


@pytest.mark.asyncio
async def test_subscribe_plan_raises_when_plan_not_found() -> None:
    with patch("app.modules.billing.service.PlanRepository") as plan_repo_cls:
        plan_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)

        with pytest.raises(PlanNotFoundError):
            await service.subscribe_plan(AsyncMock(), tenant_id=uuid.uuid4(), plan_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_subscribe_plan_creates_when_no_existing_assignment() -> None:
    tenant_id = uuid.uuid4()
    plan = _plan()
    with (
        patch("app.modules.billing.service.PlanRepository") as plan_repo_cls,
        patch("app.modules.billing.service.OrganizationPlanRepository") as org_plan_repo_cls,
    ):
        plan_repo_cls.return_value.get_by_id = AsyncMock(return_value=plan)
        org_plan_repo = org_plan_repo_cls.return_value
        org_plan_repo.get_active = AsyncMock(return_value=None)
        org_plan_repo.create = AsyncMock()

        result = await service.subscribe_plan(AsyncMock(), tenant_id=tenant_id, plan_id=plan.id)

        assert result is plan
        org_plan_repo.create.assert_awaited_once_with(tenant_id=tenant_id, plan_id=plan.id)


@pytest.mark.asyncio
async def test_subscribe_plan_switches_existing_assignment() -> None:
    tenant_id = uuid.uuid4()
    new_plan = _plan()
    existing = _organization_plan(tenant_id=tenant_id)
    with (
        patch("app.modules.billing.service.PlanRepository") as plan_repo_cls,
        patch("app.modules.billing.service.OrganizationPlanRepository") as org_plan_repo_cls,
    ):
        plan_repo_cls.return_value.get_by_id = AsyncMock(return_value=new_plan)
        org_plan_repo = org_plan_repo_cls.return_value
        org_plan_repo.get_active = AsyncMock(return_value=existing)
        org_plan_repo.set_plan = AsyncMock()

        result = await service.subscribe_plan(AsyncMock(), tenant_id=tenant_id, plan_id=new_plan.id)

        assert result is new_plan
        org_plan_repo.set_plan.assert_awaited_once_with(existing, plan_id=new_plan.id)


# --- get_usage ---


@pytest.mark.asyncio
async def test_get_usage_computes_percent_within_quota() -> None:
    plan = _plan(monthly_event_quota=100)
    with (
        patch(
            "app.modules.billing.service._ensure_organization_plan", AsyncMock(return_value=plan)
        ),
        patch("app.modules.billing.service.UsageEventRepository") as usage_repo_cls,
    ):
        usage_repo_cls.return_value.count_by_type_since = AsyncMock(
            return_value={UsageEventType.IN: 10, UsageEventType.OUT: 20}
        )

        summary = await service.get_usage(AsyncMock(), tenant_id=uuid.uuid4())

        assert summary.event_in_count == 10
        assert summary.event_out_count == 20
        assert summary.total_event_count == 30
        assert summary.quota_used_percent == 30.0
        assert summary.over_quota is False


@pytest.mark.asyncio
async def test_get_usage_over_quota_true_when_exceeded() -> None:
    plan = _plan(monthly_event_quota=10)
    with (
        patch(
            "app.modules.billing.service._ensure_organization_plan", AsyncMock(return_value=plan)
        ),
        patch("app.modules.billing.service.UsageEventRepository") as usage_repo_cls,
    ):
        usage_repo_cls.return_value.count_by_type_since = AsyncMock(
            return_value={UsageEventType.IN: 6, UsageEventType.OUT: 6}
        )

        summary = await service.get_usage(AsyncMock(), tenant_id=uuid.uuid4())

        assert summary.total_event_count == 12
        assert summary.over_quota is True


@pytest.mark.asyncio
async def test_get_usage_unlimited_quota_returns_none_percent_never_over() -> None:
    plan = _plan(monthly_event_quota=None)
    with (
        patch(
            "app.modules.billing.service._ensure_organization_plan", AsyncMock(return_value=plan)
        ),
        patch("app.modules.billing.service.UsageEventRepository") as usage_repo_cls,
    ):
        usage_repo_cls.return_value.count_by_type_since = AsyncMock(
            return_value={UsageEventType.IN: 5_000, UsageEventType.OUT: 5_000}
        )

        summary = await service.get_usage(AsyncMock(), tenant_id=uuid.uuid4())

        assert summary.quota_used_percent is None
        assert summary.over_quota is False


@pytest.mark.asyncio
async def test_get_usage_no_events_returns_zero_counts() -> None:
    plan = _plan(monthly_event_quota=100)
    with (
        patch(
            "app.modules.billing.service._ensure_organization_plan", AsyncMock(return_value=plan)
        ),
        patch("app.modules.billing.service.UsageEventRepository") as usage_repo_cls,
    ):
        usage_repo_cls.return_value.count_by_type_since = AsyncMock(return_value={})

        summary = await service.get_usage(AsyncMock(), tenant_id=uuid.uuid4())

        assert summary.event_in_count == 0
        assert summary.event_out_count == 0
        assert summary.quota_used_percent == 0.0


# --- create_usage_event ---


@pytest.mark.asyncio
async def test_create_usage_event_delegates_to_repository() -> None:
    with patch("app.modules.billing.service.UsageEventRepository") as repo_cls:
        repo_cls.return_value.create = AsyncMock()

        await service.create_usage_event(
            AsyncMock(),
            tenant_id=uuid.uuid4(),
            event_type=UsageEventType.OUT,
            bot_id=uuid.uuid4(),
            destination_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            delivery_log_id=uuid.uuid4(),
        )

        repo_cls.return_value.create.assert_awaited_once()


# --- enqueue_event_in / enqueue_event_out ---


def test_enqueue_event_in_delays_task_with_expected_kwargs() -> None:
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    task_mock = MagicMock()
    with patch("app.modules.billing.tasks.record_usage_event", task_mock):
        service.enqueue_event_in(tenant_id=tenant_id, bot_id=bot_id)

        task_mock.delay.assert_called_once_with(
            tenant_id=str(tenant_id), event_type="in", bot_id=str(bot_id)
        )


def test_enqueue_event_out_delays_task_with_expected_kwargs() -> None:
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    message_id = uuid.uuid4()
    delivery_log_id = uuid.uuid4()
    task_mock = MagicMock()
    with patch("app.modules.billing.tasks.record_usage_event", task_mock):
        service.enqueue_event_out(
            tenant_id=tenant_id,
            bot_id=bot_id,
            destination_id=destination_id,
            message_id=message_id,
            delivery_log_id=delivery_log_id,
        )

        task_mock.delay.assert_called_once_with(
            tenant_id=str(tenant_id),
            event_type="out",
            bot_id=str(bot_id),
            destination_id=str(destination_id),
            message_id=str(message_id),
            delivery_log_id=str(delivery_log_id),
        )


def test_current_utc_month_bounds_handles_december_rollover() -> None:
    now = datetime(2026, 12, 15, 10, 30, tzinfo=UTC)

    start, end = service._current_utc_month_bounds(now)

    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


# --- get_purge_targets / purge_usage_events_batch (retention) ---


@pytest.mark.asyncio
async def test_get_purge_targets_delegates_to_repository() -> None:
    targets = [(uuid.uuid4(), 30), (uuid.uuid4(), 90)]
    with patch("app.modules.billing.service.OrganizationPlanRepository") as repo_cls:
        repo_cls.return_value.list_active_with_retention = AsyncMock(return_value=targets)

        result = await service.get_purge_targets(AsyncMock())

        assert result == targets


@pytest.mark.asyncio
async def test_purge_usage_events_batch_delegates_to_repository() -> None:
    tenant_id = uuid.uuid4()
    before = datetime.now(UTC)
    with patch("app.modules.billing.service.UsageEventRepository") as repo_cls:
        repo_cls.return_value.delete_before = AsyncMock(return_value=42)

        result = await service.purge_usage_events_batch(
            AsyncMock(), tenant_id=tenant_id, before=before, batch_size=1000
        )

        assert result == 42
        repo_cls.return_value.delete_before.assert_awaited_once_with(
            tenant_id=tenant_id, before=before, batch_size=1000
        )
