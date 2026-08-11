"""Module `auth` - the service interface exposed to other modules.

Scope: authentication (register, login, refresh, logout) and user/role identity.

Cross-module communication MUST go through the interface exposed here - other
modules (including `app.core.deps`) MUST NOT
`from app.modules.auth.model import User` directly.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.model import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import revoke_tenant_refresh_tokens


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Used by `app.core.deps.get_current_user` to load the user from the JWT `sub` claim."""
    return await UserRepository(session).get_by_id(user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Used by `app.modules.organizations` when adding a member by email."""
    return await UserRepository(session).get_by_email(email)


__all__ = [
    "get_user_by_id",
    "get_user_by_email",
    "revoke_tenant_refresh_tokens",
    "User",
]
