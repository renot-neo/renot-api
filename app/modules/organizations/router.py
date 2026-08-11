"""Router module `organizations`.

Endpoints: `GET/POST /organizations`, `POST /organizations/{id}/members`,
`PATCH /organizations/{id}/members/{user_id}/role`,
`DELETE /organizations/{id}`.

Authorization for member management (POST/PATCH members, DELETE organization)
is scoped to the organization from the path `{id}` (not the active
`tenant_id` in the JWT via `core.deps.require_permission`) - see
`app.modules.organizations.service._assert_can_manage_members`/
`delete_organization` - because a user can manage/delete any organization
they own/admin without first having to "switch" their active tenant context.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.pagination import PageParams, PaginatedResponse, pagination_params
from app.core.response import Envelope, success_envelope
from app.modules.auth import User
from app.modules.organizations import service
from app.modules.organizations.schema import (
    MembershipCreate,
    MembershipListItem,
    MembershipResponse,
    MembershipRoleUpdate,
    OrganizationCreate,
    OrganizationResponse,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get(
    "",
    response_model=Envelope[PaginatedResponse[OrganizationResponse]],
    summary="List the current user's organizations",
    description="Lists the organizations the logged-in user is a member of "
    "(paginated via the `page`/`page_size` query params).",
)
async def list_organizations(
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_user_organizations(
        session, user_id=current_user.id, page_params=page_params
    )
    data = PaginatedResponse[OrganizationResponse].from_page(
        page, OrganizationResponse.model_validate
    )
    return success_envelope(data, request=request)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[OrganizationResponse],
    summary="Create a new organization",
    description="The user who creates the organization automatically becomes "
    "a member with the `owner` role.",
)
async def create_organization(
    data: OrganizationCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    organization = await service.create_organization(
        session, name=data.name, owner_user_id=current_user.id
    )
    await session.commit()
    return success_envelope(OrganizationResponse.model_validate(organization), request=request)


@router.post(
    "/{organization_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[MembershipResponse],
    summary="Add a member to the organization",
    description="Adds a registered user (looked up by email) as a member of "
    "the organization with the given role. Only this organization's "
    "owner/admin can do this.",
)
async def add_member(
    organization_id: uuid.UUID,
    data: MembershipCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    membership = await service.add_member(
        session,
        organization_id=organization_id,
        actor_user_id=current_user.id,
        email=data.email,
        role=data.role,
    )
    await session.commit()
    return success_envelope(MembershipResponse.model_validate(membership), request=request)


@router.get(
    "/{organization_id}/members",
    response_model=Envelope[PaginatedResponse[MembershipListItem]],
    summary="List the organization's members",
    description="Lists this organization's members (id, email, role, "
    "created_at), paginated. Any member of this organization can access it - "
    "any role, not just owner/admin (unlike the other member-management "
    "endpoints).",
)
async def list_members(
    organization_id: uuid.UUID,
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_memberships(
        session,
        organization_id=organization_id,
        actor_user_id=current_user.id,
        page_params=page_params,
    )
    data = PaginatedResponse[MembershipListItem].from_page(
        page,
        lambda pair: MembershipListItem(
            id=pair[0].id,
            organization_id=pair[0].organization_id,
            user_id=pair[0].user_id,
            email=pair[1],
            role=pair[0].role,
            created_at=pair[0].created_at,
        ),
    )
    return success_envelope(data, request=request)


@router.delete(
    "/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an organization",
    description="Soft-deletes this organization along with all its "
    "Bots/BotAssignments and any pending (not-yet-sent) scheduled messages "
    "(cascade). Destinations, subscriptions, plans, templates, and already-"
    "sent messages are NOT deleted (they become orphaned - no longer "
    "reachable through any API). Only this organization's owner can do this "
    "- see `service.delete_organization`.",
)
async def delete_organization(
    organization_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_organization(
        session, organization_id=organization_id, actor_user_id=current_user.id
    )
    await session.commit()


@router.patch(
    "/{organization_id}/members/{user_id}/role",
    response_model=Envelope[MembershipResponse],
    summary="Change a member's role",
    description="Changes an organization member's role. Only owner/admin can "
    "do this; the organization must always keep at least one owner.",
)
async def update_member_role(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    data: MembershipRoleUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    membership = await service.update_member_role(
        session,
        organization_id=organization_id,
        actor_user_id=current_user.id,
        target_user_id=user_id,
        new_role=data.role,
    )
    await session.commit()
    return success_envelope(MembershipResponse.model_validate(membership), request=request)
