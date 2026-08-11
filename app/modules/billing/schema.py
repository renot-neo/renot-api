"""Pydantic schemas for the `billing` module.

Scope: plans, usage metering, and retention.

`Plan`/`OrganizationPlan` have no create/update endpoint in this MVP (only
`GET /billing/plans`, `POST /billing/subscribe-plan`, `GET /billing/usage`)
- so there's no `PlanCreate`/`PlanUpdate` here, only `PlanResponse` (read)
and `SubscribePlanRequest` (plan-assignment input).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    monthly_event_quota: int | None = Field(
        description="`null` = no practical limit (no quota enforcement in this MVP yet)."
    )
    price: Decimal | None = Field(
        description="`null` = not yet priced (payment gateway integration doesn't exist yet)."
    )
    is_default: bool
    retention_days: int | None = Field(
        description="How many days `UsageEvent`/delivery history is kept "
        "before automatic deletion. `null` = kept forever."
    )


class SubscribePlanRequest(BaseModel):
    plan_id: uuid.UUID


class UsageResponse(BaseModel):
    """Current-month (UTC calendar month) usage summary for the active

    org (tenant JWT). `over_quota` is purely informational in this MVP
    (warn-only policy - no blocking of message sends/webhooks even past
    quota, see the design notes in `modules/billing/model.py`).
    """

    plan: PlanResponse
    period_start: datetime
    period_end: datetime
    event_in_count: int
    event_out_count: int
    total_event_count: int
    quota_used_percent: float | None = Field(
        description="`null` if the plan has no `monthly_event_quota` (unlimited)."
    )
    over_quota: bool
