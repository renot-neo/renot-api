"""Data access for the `auth` module.

Scope: authentication (register, login, refresh, logout) and user/role identity.

`users`/`refresh_tokens` are non-tenant - there's no `tenant_id` filter here,
and no `active()`/`with_deleted()` split like the tenant-scoped repositories
have, since `users` has no soft-delete concept.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.model import RefreshToken, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, *, email: str, password_hash: str, full_name: str) -> User:
        user = User(email=email.lower(), password_hash=password_hash, full_name=full_name)
        self._session.add(user)
        await self._session.flush()
        return user


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        tenant_id: uuid.UUID | None = None,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at, tenant_id=tenant_id
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_active_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(UTC)
        await self._session.flush()

    async def revoke_all_for_tenant(self, *, tenant_id: uuid.UUID) -> None:
        """Bulk-revoke every active `RefreshToken` (`revoked_at IS NULL`)

        whose `tenant_id` is this organization - called from the
        organization-delete cascade (see `service.revoke_tenant_refresh_tokens`),
        so `POST /auth/refresh` can no longer bring back a tenant context for
        an organization that's been deleted (`stored.tenant_id` is passed
        through as-is in `service.refresh`, which never re-validates that the
        org is still active). A bulk `UPDATE` (rather than a per-row loop
        like `soft_delete` elsewhere) because no individual token object is
        needed here - a single statement is enough.
        """
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.tenant_id == tenant_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)
