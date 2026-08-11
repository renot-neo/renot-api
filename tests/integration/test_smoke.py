"""Smoke test for the `tests/support/db.py` fixture foundation itself -

not testing any module's business logic, just that
the real-Postgres-container + savepoint-rollback + authenticated-client
plumbing actually works end-to-end. If this file breaks, every other
integration/feature test is unreliable regardless of what it claims to
cover.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.model import User
from app.modules.organizations.model import Organization


async def test_db_session_can_query_real_postgres(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


async def test_test_organization_and_owner_are_persisted_within_transaction(
    db_session: AsyncSession, test_organization: Organization, test_user_owner: User
) -> None:
    row = await db_session.execute(
        text("SELECT name FROM organizations WHERE id = :id"), {"id": test_organization.id}
    )
    assert row.scalar_one() == "Test Organization"

    row = await db_session.execute(
        text("SELECT email FROM users WHERE id = :id"), {"id": test_user_owner.id}
    )
    assert row.scalar_one() == test_user_owner.email


async def test_client_as_owner_hits_real_app_and_shares_the_same_transaction(
    client_as_owner: AsyncClient, test_organization: Organization
) -> None:
    response = await client_as_owner.get("/api/v1/organizations")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["items"][0]["id"] == str(test_organization.id)


async def test_client_as_member_is_forbidden_from_owner_only_action(
    client_as_member: AsyncClient, test_organization: Organization
) -> None:
    response = await client_as_member.post(
        f"/api/v1/organizations/{test_organization.id}/members",
        json={"email": "someone-else@example.com", "role": "member"},
    )
    assert response.status_code == 403
