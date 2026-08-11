"""Business logic for the `organizations` module.

Scope: organizations, membership, and role-based permissions (owner/admin/member).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.organizations.exceptions import (
    AlreadyMemberError,
    InsufficientPermissionError,
    LastOwnerError,
    MembershipNotFoundError,
    MemberUserNotFoundError,
    NotOrganizationMemberError,
    OrganizationNotFoundError,
)
from app.modules.organizations.model import Organization, OrganizationMembership, OrganizationRole
from app.modules.organizations.repository import MembershipRepository, OrganizationRepository

# Role -> permission set.
#
# `organization:manage_members` covers the member-management endpoints
# (POST .../members, PATCH .../members/{user_id}/role), following the
# "manage X" pattern (owner+admin only, member cannot) used consistently
# across the other permissions below.
#
# `bot:view` is granted to every role (including MEMBER) so `modules/bots`
# has a permission check for its read endpoints (GET /bots, GET /bots/{id})
# via `require_permission`, separate from `bot:manage` (create/update/delete,
# owner+admin only). MEMBER needs to see what bots exist in the org in order
# to `message:send` to the bots assigned to them.
#
# `destination:view` follows the same pattern - MEMBER gets `destination:view`
# but not `destination:manage` (create/update/delete/manage subscriptions,
# owner+admin only) - destinations are read-only for MEMBER.
#
# `message:send`/`log:view` are still org-wide at the permission-table level
# (this table is only role -> permission, it doesn't know about individual
# bots) - the actual "only bots assigned to me" scoping for MEMBER is
# implemented in `modules/messaging/service.py::_assert_bot_access`, via
# `BotAssignment` (`modules/bots/model.py`) - not in this table.
# `bot:view`/`destination:view` are DELIBERATELY NOT scoped the same way for
# MEMBER - MEMBER can still see every bot/destination in the org; only
# sending messages/viewing logs is restricted to assigned bots.
#
# `organization:delete` backs `DELETE /organizations/{id}`. It's
# deliberately SEPARATE from `organization:manage_members` (held by
# owner+admin) - deleting an entire tenant is the most destructive action in
# this system, with a much larger blast radius than managing members, so
# only OWNER is allowed to do it.
ROLE_PERMISSIONS: dict[OrganizationRole, frozenset[str]] = {
    OrganizationRole.OWNER: frozenset(
        {
            "billing:manage",
            "bot:manage",
            "bot:view",
            "destination:manage",
            "destination:view",
            "message:send",
            "log:view",
            "organization:manage_members",
            "organization:delete",
        }
    ),
    OrganizationRole.ADMIN: frozenset(
        {
            "bot:manage",
            "bot:view",
            "destination:manage",
            "destination:view",
            "message:send",
            "log:view",
            "organization:manage_members",
        }
    ),
    OrganizationRole.MEMBER: frozenset(
        {"bot:view", "destination:view", "message:send", "log:view"}
    ),
}


def has_permission(role: OrganizationRole, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


async def is_organization_active(session: AsyncSession, organization_id: uuid.UUID) -> bool:
    """Used by `core.deps.require_permission` (the central guard) - the

    single point that checks "organization hasn't been soft-deleted",
    exercised by EVERY tenant-scoped endpoint through `require_permission`,
    so no individual service function has to remember to check this on its
    own (this used to be missing on `update_member_role` until it was
    refactored to go through `_assert_can_manage_members` below).
    """
    return await OrganizationRepository(session).get_active(organization_id) is not None


async def _assert_can_manage_members(
    session: AsyncSession, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """Used by `add_member`/`update_member_role` - the member-management

    endpoints are scoped to the organization from the path `{id}` (not the
    active tenant_id JWT via `core.deps.require_permission`, see the
    `router.py` docstring), so the "organization is active" guard doesn't
    run through `require_permission` for these two endpoints - it must be
    checked explicitly here too. This check used to live only in
    `add_member` (duplicated, not in a shared helper) - `update_member_role`
    used to slip through with no check at all until it was fixed here.
    """
    if await OrganizationRepository(session).get_active(organization_id) is None:
        raise OrganizationNotFoundError()

    membership = await MembershipRepository(session).get(
        organization_id=organization_id, user_id=actor_user_id
    )
    if membership is None:
        raise NotOrganizationMemberError()
    if not has_permission(membership.role, "organization:manage_members"):
        raise InsufficientPermissionError()


async def create_organization(
    session: AsyncSession, *, name: str, owner_user_id: uuid.UUID
) -> Organization:
    organizations = OrganizationRepository(session)
    memberships = MembershipRepository(session)
    organization = await organizations.create(name=name)
    await memberships.create(
        organization_id=organization.id, user_id=owner_user_id, role=OrganizationRole.OWNER
    )
    return organization


async def list_user_organizations(
    session: AsyncSession, *, user_id: uuid.UUID, page_params: PageParams
) -> Page[Organization]:
    """Pagination is driven from `OrganizationMembership` (not `Organization`

    directly) - a user can only see the orgs they're a member of, so
    membership is the table that's naturally scoped to this user. The
    organization lookup per membership is still done per-item (not a JOIN,
    consistent with this repo's "plain FK column + explicit query" style -
    see the `billing/repository.py` docstring), but it's now BOUNDED to a
    single page's worth of items (at most `MAX_PAGE_SIZE`), not an unbounded
    N+1 like before.

    Edge case (not fixed, out of scope): `total` is computed from the
    membership count, not from organizations that are actually still active
    - if a membership points at an organization that's been soft-deleted
    (the membership row itself isn't cascade-soft-deleted), `total`/
    `total_pages` can end up slightly larger than the number of items that
    actually appear on that page.
    """
    memberships_page = await MembershipRepository(session).list_for_user(
        user_id=user_id, params=page_params
    )
    organizations = OrganizationRepository(session)
    items = []
    for membership in memberships_page.items:
        organization = await organizations.get_active(membership.organization_id)
        if organization is not None:
            items.append(organization)
    return Page(
        items=items,
        total=memberships_page.total,
        page=memberships_page.page,
        page_size=memberships_page.page_size,
    )


async def get_membership(
    session: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
) -> OrganizationMembership | None:
    return await MembershipRepository(session).get(organization_id=organization_id, user_id=user_id)


async def list_memberships(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    page_params: PageParams,
) -> Page[tuple[OrganizationMembership, str]]:
    """`GET /organizations/{id}/members` - paginated, scoped to the path

    `{id}` (not `require_permission`/the active tenant_id JWT, same pattern
    as `add_member`/`update_member_role`/`delete_organization`).
    Authorization is DELIBERATELY just "the actor is a member of this
    organization" (any role) - it doesn't reuse `organization:manage_members`
    (owner/admin only), since member-list visibility isn't restricted for
    MEMBER, same pattern as the org-wide `bot:view`/`destination:view` (see
    the `ROLE_PERMISSIONS` comment above).

    Each item is enriched with the user's email (`app.modules.auth.get_user_by_id`,
    local import - same pattern as `add_member` to break the auth<->organizations
    cycle) because `MembershipResponse`/`OrganizationMembership` on their own
    only have `user_id`, which isn't very useful for a member-list UI.
    """
    from app.modules.auth import get_user_by_id

    if await OrganizationRepository(session).get_active(organization_id) is None:
        raise OrganizationNotFoundError()

    memberships = MembershipRepository(session)
    if await memberships.get(organization_id=organization_id, user_id=actor_user_id) is None:
        raise NotOrganizationMemberError()

    memberships_page = await memberships.list_page_for_organization(
        organization_id, params=page_params
    )
    items = []
    for membership in memberships_page.items:
        user = await get_user_by_id(session, membership.user_id)
        # `user_id` is a FK to `users.id` and `users` has no soft-delete (see
        # the `auth/repository.py` docstring) - `user is None` should never
        # happen, but this stays defensive rather than 500ing on anomalous data.
        items.append((membership, user.email if user is not None else ""))
    return Page(
        items=items,
        total=memberships_page.total,
        page=memberships_page.page,
        page_size=memberships_page.page_size,
    )


async def add_member(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    email: str,
    role: OrganizationRole,
) -> OrganizationMembership:
    # Local import to break the modules/auth <-> modules/organizations import
    # cycle (same note as in modules/auth/service.py).
    from app.modules.auth import get_user_by_email

    memberships = MembershipRepository(session)

    # The active-organization check + actor permission check are now
    # combined (see the `_assert_can_manage_members` docstring) - no
    # separate/duplicated `get_active` check here anymore.
    await _assert_can_manage_members(
        session, organization_id=organization_id, actor_user_id=actor_user_id
    )

    user = await get_user_by_email(session, email)
    if user is None:
        raise MemberUserNotFoundError()

    if await memberships.get(organization_id=organization_id, user_id=user.id) is not None:
        raise AlreadyMemberError()

    return await memberships.create(organization_id=organization_id, user_id=user.id, role=role)


async def update_member_role(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_role: OrganizationRole,
) -> OrganizationMembership:
    memberships = MembershipRepository(session)

    await _assert_can_manage_members(
        session, organization_id=organization_id, actor_user_id=actor_user_id
    )

    membership = await memberships.get(organization_id=organization_id, user_id=target_user_id)
    if membership is None:
        raise MembershipNotFoundError()

    # An organization must always keep at least one owner.
    if membership.role == OrganizationRole.OWNER and new_role != OrganizationRole.OWNER:
        owner_count = await memberships.count_owners(organization_id)
        if owner_count <= 1:
            raise LastOwnerError()

    membership.role = new_role
    await session.flush()
    return membership


async def delete_organization(
    session: AsyncSession, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """`DELETE /organizations/{id}` - soft-deletes the organization with a

    hybrid cascade (not "soft-delete every tenant-scoped entity", not
    "orphan everything"):

    - `Bot`+`BotAssignment` (`modules.bots.cascade_delete_for_organization`)
      and any not-yet-dispatched `Message` (`modules.messaging.
      cascade_delete_pending_messages`) MUST be soft-deleted synchronously
      here - both have tenant-agnostic access paths (inbound Telegram
      webhooks, the Celery beat scheduled dispatcher) that never go through
      the "organization is active" guard in `core.deps.require_permission`.
    - `Destination`/`BotDestinationSubscription`/`OrganizationPlan`/
      `MessageTemplate`/historical messages are deliberately LEFT AS-IS
      (orphaned) - every access path to them goes through
      `require_permission`, so that guard alone is enough once
      `Organization.deleted_at` is set.

    `OrganizationMembership` is hard-deleted (not soft-deleted - consistent
    with this module's "leaving an org = delete the row" pattern,
    `MembershipRepository.delete`), and every `RefreshToken` whose tenant_id
    is this organization is revoked (`modules.auth.revoke_tenant_refresh_tokens`)
    so `POST /auth/refresh` can no longer bring back a tenant context for an
    organization that's been deleted.

    Owner-only (`organization:delete`, separate from `organization:manage_members`
    which admin also holds - see the `ROLE_PERMISSIONS` comment).
    """
    # Local import to break an import cycle - same pattern as
    # `from app.modules.auth import get_user_by_email` in `add_member`
    # above. Safe: `bots`/`messaging` import `app.modules.organizations` at
    # module level, but `organizations` itself never imports them at module
    # level - only inside this function, executed when it's called (not at
    # import time), so there's no cycle.
    from app.modules.auth import revoke_tenant_refresh_tokens
    from app.modules.bots import cascade_delete_for_organization
    from app.modules.messaging import cascade_delete_pending_messages

    organizations = OrganizationRepository(session)
    memberships = MembershipRepository(session)

    organization = await organizations.get_active(organization_id)
    if organization is None:
        raise OrganizationNotFoundError()

    membership = await memberships.get(organization_id=organization_id, user_id=actor_user_id)
    if membership is None:
        raise NotOrganizationMemberError()
    if not has_permission(membership.role, "organization:delete"):
        raise InsufficientPermissionError()

    await cascade_delete_for_organization(session, tenant_id=organization_id)
    await cascade_delete_pending_messages(session, tenant_id=organization_id)
    await revoke_tenant_refresh_tokens(session, tenant_id=organization_id)

    for member in await memberships.list_for_organization(organization_id):
        await memberships.delete(member)

    await organizations.soft_delete(organization)
