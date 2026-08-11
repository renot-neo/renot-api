"""Pydantic schemas for the `organizations` module.

Scope: organizations, membership, and role-based permissions (owner/admin/member).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.organizations.model import OrganizationRole


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class MembershipCreate(BaseModel):
    email: EmailStr
    role: OrganizationRole = OrganizationRole.MEMBER


class MembershipRoleUpdate(BaseModel):
    role: OrganizationRole


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: OrganizationRole
    created_at: datetime


class MembershipListItem(BaseModel):
    """The `GET /organizations/{id}/members` item shape - different from

    `MembershipResponse` (used by POST/PATCH members) because it needs
    `email` (enriched in `service.list_memberships`, not a direct
    `OrganizationMembership` attribute) - so it does NOT use
    `from_attributes`, and is built manually from a `(membership, email)`
    pair in the router.
    """

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    role: OrganizationRole
    created_at: datetime
