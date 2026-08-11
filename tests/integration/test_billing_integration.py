"""Integration tests for the `app.modules.billing` router.

Real Postgres - including the seeded "Default" `Plan` row (inserted by the
`282eb43ad43c_create_billing_tables` data migration, run for real as part
of `alembic upgrade head` in `tests/support/db.py`'s `postgres_container`
fixture - not something this test file seeds itself).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.model import Organization


async def test_list_plans_shows_seeded_default_plan(client_as_owner: AsyncClient) -> None:
    response = await client_as_owner.get("/api/v1/billing/plans")
    assert response.status_code == 200
    plans = response.json()["data"]["items"]
    assert len(plans) >= 1
    default_plan = next(p for p in plans if p["is_default"])
    assert default_plan["name"] == "Default"
    assert default_plan["monthly_event_quota"] is None
    assert default_plan["price"] is None


async def test_admin_and_member_cannot_access_billing(
    client_as_admin: AsyncClient, client_as_member: AsyncClient
) -> None:
    for client in (client_as_admin, client_as_member):
        response = await client.get("/api/v1/billing/plans")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"


async def test_get_usage_lazily_assigns_and_persists_default_plan(
    client_as_owner: AsyncClient, db_session: AsyncSession, test_organization: Organization
) -> None:
    # Sanity: no `OrganizationPlan` row yet for this fresh org.
    before = await db_session.execute(
        text("SELECT count(*) FROM organization_plans WHERE tenant_id = :tid"),
        {"tid": test_organization.id},
    )
    assert before.scalar_one() == 0

    response = await client_as_owner.get("/api/v1/billing/usage")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["plan"]["name"] == "Default"
    assert body["event_in_count"] == 0
    assert body["event_out_count"] == 0
    assert body["total_event_count"] == 0
    assert body["over_quota"] is False

    # The lazy-assign side effect must actually persist ("a GET endpoint
    # with a write side effect needs an explicit commit") - verify via a
    # fresh query, not just the response.
    after = await db_session.execute(
        text("SELECT count(*) FROM organization_plans WHERE tenant_id = :tid"),
        {"tid": test_organization.id},
    )
    assert after.scalar_one() == 1


async def test_subscribe_plan_happy_path_and_not_found(
    client_as_owner: AsyncClient, db_session: AsyncSession
) -> None:
    plans_resp = await client_as_owner.get("/api/v1/billing/plans")
    default_plan_id = next(p["id"] for p in plans_resp.json()["data"]["items"] if p["is_default"])

    ok_resp = await client_as_owner.post(
        "/api/v1/billing/subscribe-plan", json={"plan_id": default_plan_id}
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["data"]["id"] == default_plan_id

    missing_resp = await client_as_owner.post(
        "/api/v1/billing/subscribe-plan", json={"plan_id": str(uuid.uuid4())}
    )
    assert missing_resp.status_code == 404
    assert missing_resp.json()["error"]["code"] == "PLAN_NOT_FOUND"


async def test_subscribe_plan_is_owner_only(client_as_admin: AsyncClient) -> None:
    response = await client_as_admin.post(
        "/api/v1/billing/subscribe-plan", json={"plan_id": str(uuid.uuid4())}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"
