"""The single source of cross-module dependencies:
`get_db`, `get_current_user`, `get_current_tenant`, `require_permission(...)`,
`get_redis`, `get_bot_from_api_key`.

All resource access (DB session, current user, current tenant, permission
check, Redis client, bot from API key) goes through `Depends()` - never
build a global singleton that's accessed directly inside a function.

`get_bot_from_api_key` (plus the `resolve_user_from_payload`/
`resolve_tenant_from_payload`/`check_permission`/`get_optional_jwt_payload`
helpers below) supports per-bot API key auth for the message-send endpoint
used by external apps, via the `X-Bot-Api-Key` header - it stays generic
(resolving a `Bot`/`User`/permission from raw credentials, the same level of
abstraction as `get_current_user`), NOT the business rule of "which endpoint
is allowed to use dual-auth" - that's specific to `modules/messaging` (see
`modules/messaging/deps.py`, which composes the public helpers here into a
`MessagingPrincipal`). Same separation of concerns as `require_permission`
(generic, here) vs. `messaging.service._assert_bot_access` (the
per-bot-assignment business rule).

Top-level imports from `app.modules.auth`/`app.modules.organizations`/
`app.modules.bots` here are safe (not circular): none of those three modules
import `core.deps` at module level - only each one's `router.py` does, and
`modules/auth/service.py` <-> `modules/organizations/service.py` already
break their own cycle via local imports (see the comment there).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import redis.asyncio as redis
from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.core.security import JWTError, decode_token
from app.modules.auth import User, get_user_by_id
from app.modules.auth.exceptions import (
    NoActiveOrganizationError,
    TokenInvalidError,
    UserInactiveError,
)
from app.modules.bots import Bot, get_bot_by_api_key
from app.modules.organizations import get_membership, has_permission, is_organization_active
from app.modules.organizations.exceptions import (
    InsufficientPermissionError,
    NotOrganizationMemberError,
    OrganizationNotFoundError,
)

_redis_client: redis.Redis | None = None
_redis_client_loop: asyncio.AbstractEventLoop | None = None
_bearer_scheme = HTTPBearer(auto_error=True)
# `auto_error=False` - used ONLY by `get_optional_jwt_payload` (the
# dual-auth path), unlike `_bearer_scheme` above which still raises 403 when
# the `Authorization` header is missing on a regular dashboard endpoint.
_bearer_scheme_optional = HTTPBearer(auto_error=False)
_bot_api_key_scheme = APIKeyHeader(name="X-Bot-Api-Key", auto_error=False)
# The `Security(...)` marker is built once here (not inline as a parameter
# default) - ruff B008 (`fastapi.Security` isn't in `extend-immutable-calls`
# in `pyproject.toml`, unlike `fastapi.Depends`) recommends this pattern
# ("read the default from a module-level singleton variable"). It's not a
# performance concern - FastAPI only evaluates the default once when the
# function is defined, same as every other `Depends(...)` in this file.
_optional_bearer_credentials = Security(_bearer_scheme_optional)
_bot_api_key_default = Security(_bot_api_key_scheme)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        yield session


async def get_redis() -> redis.Redis:
    """Singleton `Redis` client - safe to reuse across requests within a

    single FastAPI process because that process has ONE event loop for its
    entire lifetime (unlike a Celery task, see the `messaging/tasks.py::_get_redis`
    docstring for why a singleton is NOT safe there). The singleton is
    scoped to the currently running event loop though (not just "created
    once, reused forever") - if the loop that's active NOW differs from the
    loop the old client was created on, the old client is discarded and a
    new one is built. This turned out to matter once `RateLimitMiddleware`
    (the first caller of this function - before that, `get_redis` was never
    actually called by anyone) got exercised by `tests/integration`, which
    gives each test function a fresh event loop (pytest-asyncio, the same
    reason `tests/support/db.py`'s `test_engine` uses `NullPool`) - the old
    client created on the first test's loop blew up with
    `RuntimeError: Event loop is closed` as soon as it was reused on the
    next test's loop.
    """
    global _redis_client, _redis_client_loop
    current_loop = asyncio.get_running_loop()
    if _redis_client is None or _redis_client_loop is not current_loop:
        _redis_client = redis.from_url(settings.redis.url, decode_responses=True)
        _redis_client_loop = current_loop
    return _redis_client


async def _decode_bearer_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict[str, Any]:
    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise TokenInvalidError() from exc
    if payload.get("type") != "access":
        raise TokenInvalidError()
    return payload


async def get_optional_jwt_payload(
    credentials: HTTPAuthorizationCredentials | None = _optional_bearer_credentials,
) -> dict[str, Any] | None:
    """Same as `_decode_bearer_token`, but returns `None` (instead of

    raising 403) when the `Authorization` header isn't sent at all - used by
    the dual-auth path (`modules.messaging.deps.require_permission_or_bot_api_key`),
    which needs "try JWT IF there's no API key", unlike a regular dashboard
    endpoint where JWT is always required (`_decode_bearer_token`,
    `auto_error=True`). A token that IS present but invalid/expired/not an
    `access` type still raises `TokenInvalidError` as usual - "optional"
    here is only about the header being absent, not about a bad token.
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise TokenInvalidError() from exc
    if payload.get("type") != "access":
        raise TokenInvalidError()
    return payload


async def resolve_user_from_payload(session: AsyncSession, payload: dict[str, Any]) -> User:
    """Core logic to resolve a `User` from the access token's `sub` claim -

    factored out of `get_current_user` (which adds the `request.state.user_id`
    side effect on top of this) so it can be reused by the dual-auth path
    (`modules.messaging.deps`) without duplicating the same logic.
    """
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenInvalidError() from exc

    user = await get_user_by_id(session, user_id)
    if user is None:
        raise TokenInvalidError()
    if not user.is_active:
        raise UserInactiveError()
    return user


def resolve_tenant_from_payload(payload: dict[str, Any]) -> uuid.UUID:
    """Core logic to resolve the active tenant from the access token's

    `tenant_id` claim - factored out of `get_current_tenant` (which adds the
    `request.state.tenant_id` side effect on top of this), for the same
    reason as `resolve_user_from_payload`.
    """
    tenant_id_raw = payload.get("tenant_id")
    if not tenant_id_raw:
        raise NoActiveOrganizationError()
    return uuid.UUID(str(tenant_id_raw))


async def get_current_user(
    request: Request,
    payload: dict[str, Any] = Depends(_decode_bearer_token),
    session: AsyncSession = Depends(get_db),
) -> User:
    user = await resolve_user_from_payload(session, payload)
    request.state.user_id = str(user.id)
    return user


async def get_current_tenant(
    request: Request,
    payload: dict[str, Any] = Depends(_decode_bearer_token),
) -> uuid.UUID:
    """The active tenant context - from the access token's `tenant_id` claim,

    set via `POST /auth/switch-organization`. Raises `NoActiveOrganizationError`
    if the user has never switched (the token still has `tenant_id: null`).
    """
    tenant_id = resolve_tenant_from_payload(payload)
    request.state.tenant_id = str(tenant_id)
    return tenant_id


async def check_permission(
    session: AsyncSession, *, user_id: uuid.UUID, tenant_id: uuid.UUID, permission: str
) -> None:
    """Core RBAC check (membership + active organization + role permission) -

    factored out of `require_permission` so it can be reused by the
    dual-auth path (`modules.messaging.deps.require_permission_or_bot_api_key`)
    without duplicating the same security logic. See the `require_permission`
    docstring below for details on the `is_organization_active` guard.
    """
    membership = await get_membership(session, user_id=user_id, organization_id=tenant_id)
    if membership is None:
        raise NotOrganizationMemberError()
    if not await is_organization_active(session, tenant_id):
        raise OrganizationNotFoundError()
    if not has_permission(membership.role, permission):
        raise InsufficientPermissionError()


def require_permission(permission: str) -> Callable[..., Awaitable[None]]:
    """RBAC + permission-based check - used as

    `Depends(require_permission("bot:create"))` in a router, instead of
    manually checking a role string inside the endpoint. The permission is
    checked against the active tenant in the JWT (`get_current_tenant`) -
    for endpoints whose authorization is scoped to an organization from a
    path parameter (instead of the active JWT tenant), see the pattern in
    `modules/organizations/service.py` (`_assert_can_manage_members`, which
    also does its own "organization is active" check below).

    The `is_organization_active` guard (via `check_permission`) here is the
    ONLY point that closes off access to EVERY tenant-scoped endpoint
    (bot/destination/messaging/billing - all of them wire up
    `require_permission` in their router) once `DELETE /organizations/{id}`
    soft-deletes an organization. Without this, a membership row (which
    isn't cascade-soft-deleted with the organization) would still make
    `get_membership` pass - `Organization.deleted_at` alone is never checked
    otherwise.
    """

    async def _checker(
        current_user: User = Depends(get_current_user),
        tenant_id: uuid.UUID = Depends(get_current_tenant),
        session: AsyncSession = Depends(get_db),
    ) -> None:
        await check_permission(
            session, user_id=current_user.id, tenant_id=tenant_id, permission=permission
        )

    return _checker


async def get_bot_from_api_key(
    api_key: str | None = _bot_api_key_default,
    session: AsyncSession = Depends(get_db),
) -> Bot | None:
    """Resolve a `Bot` from the `X-Bot-Api-Key` header - returns `None` when

    the header isn't sent at all (the calling endpoint decides whether to
    fall back to JWT, see `modules.messaging.deps.require_permission_or_bot_api_key`).
    A header that IS present but invalid, or whose bot has been soft-deleted,
    still raises `BotApiKeyInvalidError` (401, from `modules.bots.get_bot_by_api_key`)
    - NOT `None` - so a wrong key never silently falls through to the JWT
    path and gets treated as "no credentials at all".
    """
    if api_key is None:
        return None
    return await get_bot_by_api_key(session, api_key)
