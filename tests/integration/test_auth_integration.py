"""Integration tests for the `app.modules.auth` router.

Real Postgres, no mocked repository - HTTP client -> router -> service ->
real DB. Covers Flow 6.1 (register/login/refresh/switch-organization).
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.model import User
from app.modules.organizations import service as org_service
from app.modules.organizations.model import Organization, OrganizationRole


async def test_register_persists_user_and_returns_201(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "new-user@example.com",
            "password": "SuperSecret123!",
            "full_name": "New User",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == "new-user@example.com"
    assert "password" not in body["data"]

    row = await db_session.execute(
        text("SELECT password_hash FROM users WHERE email = :email"),
        {"email": "new-user@example.com"},
    )
    password_hash = row.scalar_one()
    assert password_hash != "SuperSecret123!"  # never stored plaintext


async def test_register_rejects_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "SuperSecret123!", "full_name": "Dup"}
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "password": "short", "full_name": "Short"},
    )
    assert response.status_code == 422


async def test_login_returns_token_pair(client: AsyncClient) -> None:
    email = "login-flow@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SuperSecret123!", "full_name": "Login Flow"},
    )
    login_resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "SuperSecret123!"}
    )
    assert login_resp.status_code == 200
    tokens = login_resp.json()["data"]
    assert tokens["access_token"]
    assert tokens["refresh_token"]


async def test_login_rejects_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "wrongpass@example.com", "password": "SuperSecret123!", "full_name": "X"},
    )
    response = await client.post(
        "/api/v1/auth/login", json={"email": "wrongpass@example.com", "password": "nope"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_refresh_rotates_token_and_invalidates_old_one(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "refresh-flow@example.com",
            "password": "SuperSecret123!",
            "full_name": "R",
        },
    )
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "refresh-flow@example.com", "password": "SuperSecret123!"},
    )
    old_refresh = login_resp.json()["data"]["refresh_token"]

    refresh_resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()["data"]
    assert new_tokens["refresh_token"] != old_refresh

    # old refresh token must now be rejected (rotation/revocation)
    reuse_resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse_resp.status_code == 401
    assert reuse_resp.json()["error"]["code"] == "REFRESH_TOKEN_INVALID"


async def test_protected_endpoint_rejects_missing_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/organizations")
    assert response.status_code in (401, 403)


async def test_switch_organization_fails_for_non_member_succeeds_after_joining(
    client: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
    test_user_owner: User,
) -> None:
    email = "switch-flow@example.com"
    password = "SuperSecret123!"
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Switch Flow"},
    )
    new_user_id = register_resp.json()["data"]["id"]

    login_resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    access_token = login_resp.json()["data"]["access_token"]
    client.headers["Authorization"] = f"Bearer {access_token}"

    # Not a member yet - switch must fail.
    forbidden_resp = await client.post(
        "/api/v1/auth/switch-organization",
        json={"organization_id": str(test_organization.id)},
    )
    assert forbidden_resp.status_code in (403, 404)

    # Add as member directly via the service (real DB, same transaction) -
    # then the same token should be able to switch into it.
    await org_service.add_member(
        db_session,
        organization_id=test_organization.id,
        actor_user_id=test_user_owner.id,
        email=email,
        role=OrganizationRole.MEMBER,
    )
    await db_session.commit()

    switch_resp = await client.post(
        "/api/v1/auth/switch-organization",
        json={"organization_id": str(test_organization.id)},
    )
    assert switch_resp.status_code == 200
    assert switch_resp.json()["data"]["access_token"]
    assert new_user_id  # sanity: register actually returned a real id
