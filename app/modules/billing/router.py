"""Router module `billing`.

Endpoints: `GET /billing/usage`, `GET /billing/plans`,
`POST /billing/subscribe-plan`.

The router's only job: receive the request -> validate via schema -> call
the service -> return the response via the envelope. No business logic here.

Every endpoint requires `require_permission("billing:manage")` - billing is
owner-only access with no read-only exception for Admin/Member (unlike
`bot:view`/`destination:view`, which are explicitly split out for MEMBER) -
so there's no separate `billing:view` permission here; the single
`billing:manage` permission (owner-only, defined in
`modules/organizations/service.py`'s `ROLE_PERMISSIONS`) covers all three
endpoints, including the read-only ones (`GET /billing/usage`,
`GET /billing/plans`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_tenant, get_db, require_permission
from app.core.pagination import PageParams, PaginatedResponse, pagination_params
from app.core.response import Envelope, success_envelope
from app.modules.billing import service
from app.modules.billing.schema import PlanResponse, SubscribePlanRequest, UsageResponse

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get(
    "/usage",
    response_model=Envelope[UsageResponse],
    summary="Current month's usage events",
    description="Summarizes `UsageEvent`s (event_in/event_out) for the "
    "current calendar month (UTC) for the active organization, compared "
    "against its plan's quota. `over_quota` is purely informational in "
    "this MVP - no blocking of message sends/webhooks even past quota "
    "(warn-only policy).",
    dependencies=[Depends(require_permission("billing:manage"))],
)
async def get_usage(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    summary = await service.get_usage(session, tenant_id=tenant_id)
    # `get_usage` -> `_ensure_organization_plan` can have a WRITE side
    # effect (auto-assigning the default plan for an org that doesn't yet
    # have an `OrganizationPlan`, see its docstring) even though this
    # endpoint is `GET` - an explicit commit is required here, unlike other
    # purely read-only endpoints that never write.
    await session.commit()
    data = UsageResponse(
        plan=PlanResponse.model_validate(summary.plan),
        period_start=summary.period_start,
        period_end=summary.period_end,
        event_in_count=summary.event_in_count,
        event_out_count=summary.event_out_count,
        total_event_count=summary.total_event_count,
        quota_used_percent=summary.quota_used_percent,
        over_quota=summary.over_quota,
    )
    return success_envelope(data, request=request)


@router.get(
    "/plans",
    response_model=Envelope[PaginatedResponse[PlanResponse]],
    summary="List available plans",
    description="The platform's plan catalog (no Plan CRUD in the dashboard "
    "yet - rows are populated via data migration; final tiers/pricing "
    "aren't designed yet). Paginated via the `page`/`page_size` query params.",
    dependencies=[Depends(require_permission("billing:manage"))],
)
async def list_plans(
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_plans(session, page_params=page_params)
    data = PaginatedResponse[PlanResponse].from_page(page, PlanResponse.model_validate)
    return success_envelope(data, request=request)


@router.post(
    "/subscribe-plan",
    response_model=Envelope[PlanResponse],
    summary="Choose/change the active organization's plan",
    description="Assigns a plan to the active organization (JWT tenant "
    "context). There's NO payment gateway integration in this MVP - this "
    "is purely a plan-assignment change, not an actual payment/checkout.",
    dependencies=[Depends(require_permission("billing:manage"))],
)
async def subscribe_plan(
    data: SubscribePlanRequest,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    plan = await service.subscribe_plan(session, tenant_id=tenant_id, plan_id=data.plan_id)
    await session.commit()
    return success_envelope(PlanResponse.model_validate(plan), request=request)
