"""Unit tests for `app.modules.organizations.service`.

Pure logic - the repository is mocked, no real DB/network.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.pagination import Page, PageParams
from app.modules.organizations import service
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


def _membership(role: OrganizationRole, **overrides: object) -> OrganizationMembership:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "role": role,
    }
    defaults.update(overrides)
    return OrganizationMembership(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("role", "permission", "expected"),
    [
        (OrganizationRole.OWNER, "billing:manage", True),
        (OrganizationRole.ADMIN, "billing:manage", False),
        (OrganizationRole.MEMBER, "billing:manage", False),
        (OrganizationRole.ADMIN, "bot:manage", True),
        (OrganizationRole.MEMBER, "bot:manage", False),
        (OrganizationRole.ADMIN, "destination:manage", True),
        (OrganizationRole.MEMBER, "destination:manage", False),
        (OrganizationRole.OWNER, "destination:view", True),
        (OrganizationRole.MEMBER, "destination:view", True),
        (OrganizationRole.MEMBER, "message:send", True),
        (OrganizationRole.OWNER, "organization:manage_members", True),
        (OrganizationRole.ADMIN, "organization:manage_members", True),
        (OrganizationRole.MEMBER, "organization:manage_members", False),
        (OrganizationRole.OWNER, "organization:delete", True),
        (OrganizationRole.ADMIN, "organization:delete", False),
        (OrganizationRole.MEMBER, "organization:delete", False),
    ],
)
def test_has_permission_matches_role_table(
    role: OrganizationRole, permission: str, expected: bool
) -> None:
    assert service.has_permission(role, permission) is expected


@pytest.mark.asyncio
async def test_is_organization_active_reflects_repository() -> None:
    with patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls:
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        assert await service.is_organization_active(AsyncMock(), uuid.uuid4()) is True

        org_repo_cls.return_value.get_active = AsyncMock(return_value=None)
        assert await service.is_organization_active(AsyncMock(), uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_add_member_raises_when_organization_missing() -> None:
    with patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls:
        org_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(OrganizationNotFoundError):
            await service.add_member(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                email="new@example.com",
                role=OrganizationRole.MEMBER,
            )


@pytest.mark.asyncio
async def test_add_member_raises_when_actor_not_member() -> None:
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(return_value=None)

        with pytest.raises(NotOrganizationMemberError):
            await service.add_member(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                email="new@example.com",
                role=OrganizationRole.MEMBER,
            )


@pytest.mark.asyncio
async def test_add_member_raises_when_actor_is_plain_member() -> None:
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(
            return_value=_membership(OrganizationRole.MEMBER)
        )

        with pytest.raises(InsufficientPermissionError):
            await service.add_member(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                email="new@example.com",
                role=OrganizationRole.MEMBER,
            )


@pytest.mark.asyncio
async def test_add_member_raises_when_target_email_unregistered() -> None:
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
        patch("app.modules.auth.get_user_by_email", AsyncMock(return_value=None)),
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(
            return_value=_membership(OrganizationRole.OWNER)
        )

        with pytest.raises(MemberUserNotFoundError):
            await service.add_member(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                email="nobody@example.com",
                role=OrganizationRole.MEMBER,
            )


@pytest.mark.asyncio
async def test_add_member_raises_when_already_member() -> None:
    from app.modules.auth.model import User

    target_user = User(
        id=uuid.uuid4(),
        email="existing@example.com",
        password_hash="x",
        full_name="Existing",
        is_active=True,
    )
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
        patch("app.modules.auth.get_user_by_email", AsyncMock(return_value=target_user)),
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(
            return_value=_membership(OrganizationRole.OWNER)
        )

        with pytest.raises(AlreadyMemberError):
            await service.add_member(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                email="existing@example.com",
                role=OrganizationRole.MEMBER,
            )


@pytest.mark.asyncio
async def test_list_memberships_raises_when_organization_missing() -> None:
    with patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls:
        org_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(OrganizationNotFoundError):
            await service.list_memberships(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                page_params=PageParams(),
            )


@pytest.mark.asyncio
async def test_list_memberships_raises_when_actor_not_member() -> None:
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(return_value=None)

        with pytest.raises(NotOrganizationMemberError):
            await service.list_memberships(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                page_params=PageParams(),
            )


@pytest.mark.asyncio
async def test_list_memberships_allows_plain_member_and_enriches_email() -> None:
    """Unlike `add_member`/`update_member_role` - a plain MEMBER (not just

    owner/admin) must still be able to list, and each item must get its
    `email` from `app.modules.auth.get_user_by_id` (see the
    `service.list_memberships` docstring).
    """
    from app.modules.auth.model import User

    actor_id = uuid.uuid4()
    target = _membership(OrganizationRole.MEMBER, user_id=uuid.uuid4())
    target_user = User(
        id=target.user_id,
        email="member@example.com",
        password_hash="x",
        full_name="Member",
        is_active=True,
    )
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
        patch("app.modules.auth.get_user_by_id", AsyncMock(return_value=target_user)),
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(
            return_value=_membership(OrganizationRole.MEMBER)
        )
        membership_repo_cls.return_value.list_page_for_organization = AsyncMock(
            return_value=Page(items=[target], total=1, page=1, page_size=20)
        )

        page = await service.list_memberships(
            AsyncMock(),
            organization_id=uuid.uuid4(),
            actor_user_id=actor_id,
            page_params=PageParams(),
        )

        assert page.total == 1
        membership, email = page.items[0]
        assert membership is target
        assert email == "member@example.com"


@pytest.mark.asyncio
async def test_update_member_role_raises_when_organization_inactive() -> None:
    """A gap closed via `_assert_can_manage_members` - before the refactor,

    `update_member_role` never checked whether the organization was active
    at all.
    """
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository"),
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(OrganizationNotFoundError):
            await service.update_member_role(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                target_user_id=uuid.uuid4(),
                new_role=OrganizationRole.ADMIN,
            )


@pytest.mark.asyncio
async def test_update_member_role_raises_when_membership_missing() -> None:
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        # Panggilan pertama: permission check actor (owner, lolos). Panggilan
        # kedua: lookup membership target -> None (target bukan member).
        membership_repo_cls.return_value.get = AsyncMock(
            side_effect=[_membership(OrganizationRole.OWNER), None]
        )

        with pytest.raises(MembershipNotFoundError):
            await service.update_member_role(
                AsyncMock(),
                organization_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                target_user_id=uuid.uuid4(),
                new_role=OrganizationRole.ADMIN,
            )


@pytest.mark.asyncio
async def test_update_member_role_blocks_demoting_last_owner() -> None:
    org_id = uuid.uuid4()
    owner_membership = _membership(OrganizationRole.OWNER, organization_id=org_id)
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        repo = membership_repo_cls.return_value
        repo.get = AsyncMock(side_effect=[owner_membership, owner_membership])
        repo.count_owners = AsyncMock(return_value=1)

        with pytest.raises(LastOwnerError):
            await service.update_member_role(
                AsyncMock(),
                organization_id=org_id,
                actor_user_id=uuid.uuid4(),
                target_user_id=uuid.uuid4(),
                new_role=OrganizationRole.ADMIN,
            )


@pytest.mark.asyncio
async def test_update_member_role_allows_demoting_when_multiple_owners() -> None:
    org_id = uuid.uuid4()
    owner_membership = _membership(OrganizationRole.OWNER, organization_id=org_id)
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        repo = membership_repo_cls.return_value
        repo.get = AsyncMock(side_effect=[owner_membership, owner_membership])
        repo.count_owners = AsyncMock(return_value=2)

        updated = await service.update_member_role(
            AsyncMock(),
            organization_id=org_id,
            actor_user_id=uuid.uuid4(),
            target_user_id=uuid.uuid4(),
            new_role=OrganizationRole.ADMIN,
        )

        assert updated.role == OrganizationRole.ADMIN


# `delete_organization` - `revoke_tenant_refresh_tokens`/
# `cascade_delete_for_organization`/`cascade_delete_pending_messages` are
# imported LOCALLY inside the function (same pattern as `get_user_by_email`
# in `add_member`) - so they're patched in their ORIGIN module
# (`app.modules.auth`/`app.modules.bots`/`app.modules.messaging`), not
# `app.modules.organizations.service`.


@pytest.mark.asyncio
async def test_delete_organization_raises_when_organization_missing() -> None:
    with patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls:
        org_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(OrganizationNotFoundError):
            await service.delete_organization(
                AsyncMock(), organization_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_delete_organization_raises_when_actor_not_member() -> None:
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(return_value=None)

        with pytest.raises(NotOrganizationMemberError):
            await service.delete_organization(
                AsyncMock(), organization_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_delete_organization_raises_when_actor_is_admin_not_owner() -> None:
    """`organization:delete` is deliberately owner-only - unlike

    `organization:manage_members`, which admin also holds (see the
    `ROLE_PERMISSIONS` comment).
    """
    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
    ):
        org_repo_cls.return_value.get_active = AsyncMock(return_value=Organization(name="Acme"))
        membership_repo_cls.return_value.get = AsyncMock(
            return_value=_membership(OrganizationRole.ADMIN)
        )

        with pytest.raises(InsufficientPermissionError):
            await service.delete_organization(
                AsyncMock(), organization_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_delete_organization_cascades_and_soft_deletes_when_owner() -> None:
    org_id = uuid.uuid4()
    organization = Organization(id=org_id, name="Acme")
    owner_membership = _membership(OrganizationRole.OWNER, organization_id=org_id)
    other_membership = _membership(OrganizationRole.MEMBER, organization_id=org_id)

    with (
        patch("app.modules.organizations.service.OrganizationRepository") as org_repo_cls,
        patch("app.modules.organizations.service.MembershipRepository") as membership_repo_cls,
        patch("app.modules.auth.revoke_tenant_refresh_tokens", AsyncMock()) as revoke_tokens,
        patch("app.modules.bots.cascade_delete_for_organization", AsyncMock()) as cascade_bots,
        patch(
            "app.modules.messaging.cascade_delete_pending_messages", AsyncMock()
        ) as cascade_messages,
    ):
        org_repo = org_repo_cls.return_value
        org_repo.get_active = AsyncMock(return_value=organization)
        org_repo.soft_delete = AsyncMock()

        membership_repo = membership_repo_cls.return_value
        membership_repo.get = AsyncMock(return_value=owner_membership)
        membership_repo.list_for_organization = AsyncMock(
            return_value=[owner_membership, other_membership]
        )
        membership_repo.delete = AsyncMock()

        await service.delete_organization(
            AsyncMock(), organization_id=org_id, actor_user_id=owner_membership.user_id
        )

        # All three cross-module cascades are called exactly once with the
        # deleted org's tenant_id (see the `delete_organization` docstring
        # for why exactly these three, not more/fewer).
        assert cascade_bots.await_args.kwargs["tenant_id"] == org_id
        assert cascade_messages.await_args.kwargs["tenant_id"] == org_id
        assert revoke_tokens.await_args.kwargs["tenant_id"] == org_id

        # Both membership rows (the deleting owner + the other member) are
        # hard-deleted, not soft-deleted ("leaving an org = delete the row").
        assert membership_repo.delete.await_count == 2
        org_repo.soft_delete.assert_awaited_once_with(organization)
