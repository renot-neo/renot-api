"""Data access for the `organizations` module.

Scope: organizations, membership, and role-based permissions (owner/admin/member).

`OrganizationRepository.get_active()` always excludes soft-deleted rows -
there's no `with_deleted()` variant here, since no caller anywhere in the
codebase needs an audit/admin path that looks past the soft-delete.
`organization_memberships` isn't soft-deleted, so `MembershipRepository`
hard-deletes the row when a member is removed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.organizations.model import Organization, OrganizationMembership, OrganizationRole


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, name: str) -> Organization:
        organization = Organization(name=name)
        self._session.add(organization)
        await self._session.flush()
        return organization

    async def get_active(self, organization_id: uuid.UUID) -> Organization | None:
        """Default lookup: excludes soft-deleted rows."""
        stmt = select(Organization).where(
            Organization.id == organization_id, Organization.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(self, organization: Organization) -> None:
        organization.deleted_at = datetime.now(UTC)
        await self._session.flush()


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: OrganizationRole
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            organization_id=organization_id, user_id=user_id, role=role
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def get(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMembership | None:
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: uuid.UUID
    ) -> list[OrganizationMembership]:
        """Full (unpaginated) list - used internally only (`count_owners`,

        `delete_organization`'s hard-delete cascade) where ALL rows are
        needed, not a single page. The public endpoint (`GET .../members`)
        uses `list_page_for_organization` below.
        """
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_page_for_organization(
        self, organization_id: uuid.UUID, *, params: PageParams
    ) -> Page[OrganizationMembership]:
        """Paginated version - used by `service.list_memberships` (`GET

        /organizations/{id}/members`), same pattern as `list_for_user`.
        """
        base = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id
        )
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = (
            base.order_by(OrganizationMembership.created_at.desc())
            .limit(params.limit)
            .offset(params.offset)
        )
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    async def list_for_user(
        self, *, user_id: uuid.UUID, params: PageParams
    ) -> Page[OrganizationMembership]:
        base = select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
        total = (
            await self._session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        stmt = (
            base.order_by(OrganizationMembership.created_at.desc())
            .limit(params.limit)
            .offset(params.offset)
        )
        result = await self._session.execute(stmt)
        items = list(result.scalars().all())
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    async def count_owners(self, organization_id: uuid.UUID) -> int:
        memberships = await self.list_for_organization(organization_id)
        return sum(1 for m in memberships if m.role == OrganizationRole.OWNER)

    async def delete(self, membership: OrganizationMembership) -> None:
        await self._session.delete(membership)
        await self._session.flush()
