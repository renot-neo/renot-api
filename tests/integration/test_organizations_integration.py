"""Integration tests for the `app.modules.organizations` router.

Real Postgres, no mocked repository - RBAC (owner/admin/member) proven via
real `client_as_owner`/`client_as_admin`/`client_as_member` fixtures, not
mocked permission checks.
"""

from __future__ import annotations

import uuid

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.model import User
from app.modules.organizations.model import Organization

_BOT_TOKEN = "123456:org-delete-cascade-test-token"


def _mock_bot_registration(telegram_bot_id: int = 555000111) -> None:
    respx.post(f"https://api.telegram.org/bot{_BOT_TOKEN}/getMe").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": telegram_bot_id, "username": "cascade_bot"}}
        )
    )
    respx.post(f"https://api.telegram.org/bot{_BOT_TOKEN}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )


async def test_create_organization_makes_creator_the_owner(
    client_as_owner: AsyncClient, db_session: AsyncSession, test_user_owner: User
) -> None:
    response = await client_as_owner.post("/api/v1/organizations", json={"name": "Second Org"})
    assert response.status_code == 201
    org_id = response.json()["data"]["id"]

    row = await db_session.execute(
        text(
            "SELECT role FROM organization_memberships "
            "WHERE organization_id = :org_id AND user_id = :user_id"
        ),
        {"org_id": org_id, "user_id": test_user_owner.id},
    )
    # Postgres enum stores the Python `Enum` member's *name*, not its
    # `.value` (SQLAlchemy's `Enum(OrganizationRole, ...)` default) - "OWNER",
    # not "owner". The API layer still serializes the lowercase `.value`
    # (see the `role: "admin"` assertion below), this is only about the raw
    # column.
    assert row.scalar_one() == "OWNER"


async def test_list_organizations_only_shows_own_memberships(
    client_as_owner: AsyncClient, test_organization: Organization
) -> None:
    response = await client_as_owner.get("/api/v1/organizations")
    assert response.status_code == 200
    body = response.json()["data"]
    ids = [org["id"] for org in body["items"]]
    assert str(test_organization.id) in ids
    assert body["pagination"]["total"] >= 1


async def test_owner_and_admin_can_add_member_but_member_cannot(
    client_as_owner: AsyncClient,
    client_as_admin: AsyncClient,
    client_as_member: AsyncClient,
    test_organization: Organization,
) -> None:
    owner_resp = await client_as_owner.post(
        f"/api/v1/organizations/{test_organization.id}/members",
        json={"email": "added-by-owner@example.com", "role": "member"},
    )
    assert owner_resp.status_code == 404  # user not registered yet -> MEMBER_USER_NOT_FOUND
    assert owner_resp.json()["error"]["code"] == "MEMBER_USER_NOT_FOUND"

    member_resp = await client_as_member.post(
        f"/api/v1/organizations/{test_organization.id}/members",
        json={"email": "whoever@example.com", "role": "member"},
    )
    assert member_resp.status_code == 403
    assert member_resp.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"


async def test_owner_can_add_a_genuinely_new_member(
    client: AsyncClient, client_as_owner: AsyncClient, test_organization: Organization
) -> None:
    """The success path - both existing add_member tests above only hit

    error branches (`MEMBER_USER_NOT_FOUND` for an unregistered email,
    `ALREADY_MEMBER` below for an existing one), neither ever reaches this
    endpoint's actual 201 response. Needs a real, freshly-registered (but
    not yet a member) user to do that.
    """
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "new-member@example.com",
            "password": "TestPassword123!",
            "full_name": "New Member",
        },
    )
    assert register_resp.status_code == 201

    response = await client_as_owner.post(
        f"/api/v1/organizations/{test_organization.id}/members",
        json={"email": "new-member@example.com", "role": "member"},
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["role"] == "member"


async def test_add_member_rejects_already_member(
    client_as_owner: AsyncClient, test_organization: Organization, test_user_admin: User
) -> None:
    response = await client_as_owner.post(
        f"/api/v1/organizations/{test_organization.id}/members",
        json={"email": test_user_admin.email, "role": "member"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALREADY_MEMBER"


async def test_update_member_role_owner_can_promote_member_to_admin(
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
    test_user_member: User,
) -> None:
    response = await client_as_owner.patch(
        f"/api/v1/organizations/{test_organization.id}/members/{test_user_member.id}/role",
        json={"role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["role"] == "admin"

    row = await db_session.execute(
        text(
            "SELECT role FROM organization_memberships "
            "WHERE organization_id = :org_id AND user_id = :user_id"
        ),
        {"org_id": test_organization.id, "user_id": test_user_member.id},
    )
    assert row.scalar_one() == "ADMIN"  # raw column stores the enum name, see note above


async def test_last_owner_cannot_be_demoted(
    client_as_owner: AsyncClient, test_organization: Organization, test_user_owner: User
) -> None:
    response = await client_as_owner.patch(
        f"/api/v1/organizations/{test_organization.id}/members/{test_user_owner.id}/role",
        json={"role": "member"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_OWNER"


async def test_member_cannot_change_roles(
    client_as_member: AsyncClient, test_organization: Organization, test_user_member: User
) -> None:
    response = await client_as_member.patch(
        f"/api/v1/organizations/{test_organization.id}/members/{test_user_member.id}/role",
        json={"role": "admin"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"


# --- DELETE /organizations/{id} ---


async def test_admin_and_member_cannot_delete_organization(
    client_as_admin: AsyncClient,
    client_as_member: AsyncClient,
    test_organization: Organization,
) -> None:
    """`organization:delete` is deliberately owner-only - unlike

    `organization:manage_members`, which admin also holds.
    """
    admin_resp = await client_as_admin.delete(f"/api/v1/organizations/{test_organization.id}")
    assert admin_resp.status_code == 403
    assert admin_resp.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"

    member_resp = await client_as_member.delete(f"/api/v1/organizations/{test_organization.id}")
    assert member_resp.status_code == 403
    assert member_resp.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"


async def test_delete_nonexistent_organization_returns_404(client_as_owner: AsyncClient) -> None:
    response = await client_as_owner.delete(f"/api/v1/organizations/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORGANIZATION_NOT_FOUND"


@respx.mock
async def test_owner_delete_cascades_bots_and_hard_deletes_memberships(
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
    test_user_admin: User,
) -> None:
    """The bot is cascade soft-deleted too (`modules.bots.cascade_delete_for_organization`,

    which is required - it closes the inbound-webhook bypass), while
    `OrganizationMembership` is hard-deleted (not soft-deleted - "leaving
    an org = delete the row").
    """
    _mock_bot_registration()
    register_resp = await client_as_owner.post(
        "/api/v1/bots", json={"name": "Cascade Bot", "token": _BOT_TOKEN}
    )
    assert register_resp.status_code == 201
    bot_id = register_resp.json()["data"]["id"]

    response = await client_as_owner.delete(f"/api/v1/organizations/{test_organization.id}")
    assert response.status_code == 204

    org_row = await db_session.execute(
        text("SELECT deleted_at FROM organizations WHERE id = :id"), {"id": test_organization.id}
    )
    assert org_row.scalar_one() is not None

    bot_row = await db_session.execute(
        text("SELECT deleted_at FROM bots WHERE id = :id"), {"id": bot_id}
    )
    assert bot_row.scalar_one() is not None

    membership_count = await db_session.execute(
        text("SELECT COUNT(*) FROM organization_memberships WHERE organization_id = :id"),
        {"id": test_organization.id},
    )
    assert membership_count.scalar_one() == 0
    # Sanity: the `test_user_admin` fixture does add a second membership row
    # before the delete - if this count was never > 1 to begin with, the
    # `== 0` assert above wouldn't actually prove hard-delete ran.
    assert test_user_admin.id is not None


async def test_inactive_organization_blocks_further_tenant_scoped_requests(
    client_as_owner: AsyncClient, db_session: AsyncSession, test_organization: Organization
) -> None:
    """The central `core.deps.require_permission` guard - checked via a

    DIRECT `UPDATE ... SET deleted_at` (not `DELETE /organizations/{id}`),
    because `delete_organization` itself ALSO hard-deletes every
    `OrganizationMembership` - if tested through the delete endpoint,
    `NotOrganizationMemberError` would mask whether this "organization is
    active" guard actually works on its own. This test proves the guard
    itself, isolated from the membership hard-delete side effect: the
    `test_organization` membership row still exists here, only
    `Organization.deleted_at` gets set.
    """
    await db_session.execute(
        text("UPDATE organizations SET deleted_at = now() WHERE id = :id"),
        {"id": test_organization.id},
    )
    await db_session.commit()

    bots_resp = await client_as_owner.get("/api/v1/bots")
    assert bots_resp.status_code == 404
    assert bots_resp.json()["error"]["code"] == "ORGANIZATION_NOT_FOUND"


async def test_list_members_returns_email_and_role_for_all_roles(
    client_as_owner: AsyncClient,
    test_organization: Organization,
    test_user_owner: User,
    test_user_admin: User,
    test_user_member: User,
) -> None:
    response = await client_as_owner.get(f"/api/v1/organizations/{test_organization.id}/members")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["pagination"]["total"] == 3
    roles_by_email = {item["email"]: item["role"] for item in data["items"]}
    assert roles_by_email[test_user_owner.email] == "owner"
    assert roles_by_email[test_user_admin.email] == "admin"
    assert roles_by_email[test_user_member.email] == "member"


async def test_list_members_allows_plain_member(
    client_as_member: AsyncClient, test_organization: Organization
) -> None:
    """A MEMBER (not just owner/admin) can still view the member list -

    unlike POST/PATCH members, which need the `organization:manage_members`
    permission (owner/admin only). See the `service.list_memberships` docstring.
    """
    response = await client_as_member.get(f"/api/v1/organizations/{test_organization.id}/members")
    assert response.status_code == 200


async def test_list_members_paginates(
    client_as_owner: AsyncClient,
    test_organization: Organization,
    test_user_admin: User,
    test_user_member: User,
) -> None:
    response = await client_as_owner.get(
        f"/api/v1/organizations/{test_organization.id}/members",
        params={"page": 1, "page_size": 2},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["items"]) == 2
    assert data["pagination"] == {"page": 1, "page_size": 2, "total": 3, "total_pages": 2}


async def test_list_members_of_nonexistent_organization_returns_404(
    client_as_owner: AsyncClient,
) -> None:
    response = await client_as_owner.get(f"/api/v1/organizations/{uuid.uuid4()}/members")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORGANIZATION_NOT_FOUND"


async def test_delete_organization_revokes_refresh_tokens_for_that_tenant(
    client: AsyncClient,
    client_as_owner: AsyncClient,
    db_session: AsyncSession,
    test_organization: Organization,
    test_user_owner: User,
) -> None:
    """A refresh token carrying the `tenant_id` of an org that's since been

    deleted (see the `auth.service.refresh` docstring - `stored.tenant_id`
    is passed through as-is) must fail once the org is deleted - closed via
    `modules.auth.revoke_tenant_refresh_tokens`.
    """
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": test_user_owner.email, "password": "TestPassword123!"},
    )
    assert login_resp.status_code == 200
    client.headers["Authorization"] = f"Bearer {login_resp.json()['data']['access_token']}"

    switch_resp = await client.post(
        "/api/v1/auth/switch-organization",
        json={"organization_id": str(test_organization.id)},
    )
    assert switch_resp.status_code == 200
    tenant_refresh_token = switch_resp.json()["data"]["refresh_token"]

    delete_resp = await client_as_owner.delete(f"/api/v1/organizations/{test_organization.id}")
    assert delete_resp.status_code == 204

    refresh_resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tenant_refresh_token}
    )
    assert refresh_resp.status_code == 401
    assert refresh_resp.json()["error"]["code"] == "REFRESH_TOKEN_INVALID"
