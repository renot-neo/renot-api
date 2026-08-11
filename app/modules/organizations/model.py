"""SQLAlchemy models for the `organizations` module.

Scope: organizations, membership, and role-based permissions (owner/admin/member).

`Organization` is the tenant root itself - it does NOT have a `tenant_id`
pointing to itself, so it doesn't inherit `TenantScopedBase`, but it still
needs a UUID v7 id + soft-delete like every other tenant-scoped entity.

`OrganizationMembership` is the User <-> Organization pivot with a role
(owner/admin/member) - not soft-deleted (leaving an org = deleting the
membership row; there's no need to retain membership history for the MVP).
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class OrganizationRole(enum.StrEnum):
    """Per-organization role - a fixed set, not dynamically configurable in this MVP."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)


class OrganizationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id"), index=True, nullable=False
    )
    # FK to `users.id` by table name string (not by importing the `auth`
    # model), so `organizations` doesn't have to import another module's
    # internals.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )
    role: Mapped[OrganizationRole] = mapped_column(
        Enum(OrganizationRole, name="organization_role"), nullable=False
    )
