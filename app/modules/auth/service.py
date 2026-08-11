"""Business logic for the `auth` module.

Scope: authentication (register, login, refresh, logout) and user/role identity.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    JWTError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.auth.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    RefreshTokenInvalidError,
    UserInactiveError,
)
from app.modules.auth.model import User
from app.modules.auth.repository import RefreshTokenRepository, UserRepository
from app.modules.auth.schema import TokenPairResponse


def _hash_token(token: str) -> str:
    """SHA-256 fingerprint of a refresh token for O(1) lookup/revoke.

    Different case from a human password (argon2/bcrypt): a refresh token is
    already high-entropy JWT output, so it doesn't need a salted slow-hash -
    what's needed instead is a deterministic hash so it can be looked up by value.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def register(session: AsyncSession, *, email: str, password: str, full_name: str) -> User:
    users = UserRepository(session)
    if await users.get_by_email(email) is not None:
        raise EmailAlreadyRegisteredError()
    return await users.create(
        email=email, password_hash=hash_password(password), full_name=full_name
    )


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    users = UserRepository(session)
    user = await users.get_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError()
    if not user.is_active:
        raise UserInactiveError()
    return user


async def issue_token_pair(
    session: AsyncSession, *, user: User, tenant_id: uuid.UUID | None
) -> TokenPairResponse:
    tokens = RefreshTokenRepository(session)
    access_token = create_access_token(
        subject=str(user.id), tenant_id=str(tenant_id) if tenant_id else None
    )
    refresh_token_value = create_refresh_token(subject=str(user.id))
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt.refresh_token_expire_days)
    await tokens.create(
        user_id=user.id,
        token_hash=_hash_token(refresh_token_value),
        expires_at=expires_at,
        tenant_id=tenant_id,
    )
    return TokenPairResponse(access_token=access_token, refresh_token=refresh_token_value)


async def login(session: AsyncSession, *, email: str, password: str) -> TokenPairResponse:
    user = await authenticate(session, email=email, password=password)
    # No tenant context is set at login time - the user must call
    # switch-organization to get a tenant-scoped token.
    return await issue_token_pair(session, user=user, tenant_id=None)


async def refresh(session: AsyncSession, *, refresh_token: str) -> TokenPairResponse:
    tokens = RefreshTokenRepository(session)
    try:
        payload = decode_token(refresh_token)
    except JWTError as exc:
        raise RefreshTokenInvalidError() from exc
    if payload.get("type") != "refresh":
        raise RefreshTokenInvalidError()

    stored = await tokens.get_active_by_hash(_hash_token(refresh_token))
    if stored is None or stored.expires_at < datetime.now(UTC):
        raise RefreshTokenInvalidError()

    users = UserRepository(session)
    user = await users.get_by_id(stored.user_id)
    if user is None or not user.is_active:
        raise RefreshTokenInvalidError()

    # Rotate: revoke the old token, issue a new pair (prevents replay),
    # keeping the same tenant context as the old token.
    await tokens.revoke(stored)
    return await issue_token_pair(session, user=user, tenant_id=stored.tenant_id)


async def revoke_tenant_refresh_tokens(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Used by `modules/organizations.delete_organization` (the

    organization-delete cascade) via the public `app.modules.auth` interface
    - revokes every refresh token whose `tenant_id` is this organization.
    """
    await RefreshTokenRepository(session).revoke_all_for_tenant(tenant_id=tenant_id)


async def switch_organization(
    session: AsyncSession, *, user: User, organization_id: uuid.UUID
) -> TokenPairResponse:
    # Local import (not top-level) to avoid import-time coupling between
    # modules - called through the public service interface
    # `app.modules.organizations`.
    from app.modules.organizations import get_membership
    from app.modules.organizations.exceptions import NotOrganizationMemberError

    membership = await get_membership(session, user_id=user.id, organization_id=organization_id)
    if membership is None:
        raise NotOrganizationMemberError()

    return await issue_token_pair(session, user=user, tenant_id=organization_id)
