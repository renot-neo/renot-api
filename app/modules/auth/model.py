"""SQLAlchemy models for the `auth` module.

Scope: authentication (register, login, refresh, logout) and user/role identity.

`User` is non-tenant (a single user can belong to many organizations via
`modules.organizations.OrganizationMembership`) - it uses the plain `Base`,
not `TenantScopedBase`. `RefreshToken` is stored hashed so it can be revoked
without ever persisting the raw token.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A platform user account. No soft-delete for `users` - account

    deletion (if ever needed) is a hard-delete concern (compliance/privacy)
    to be designed separately, not assumed here.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A refresh token, stored hashed (never plaintext) - revoked by setting

    `revoked_at`, not by deleting the row, so it stays auditable.

    `tenant_id` (nullable) records the active organization context at the
    time the token was issued (via login or switch-organization), so
    `/auth/refresh` can preserve the tenant context without the user having
    to switch-organization again every time the access token expires.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
