"""FastAPI dependencies specific to the `messaging` module - dual-auth

(dashboard JWT OR per-bot API key) for the "send a message from an external
app" endpoint (per-bot API key via the `X-Bot-Api-Key` header).

Deliberately NOT placed in `core/deps.py` (which is reserved for a fixed,
generic set of resource-access dependencies:
`get_db`/`get_current_user`/`get_current_tenant`/`require_permission`/
`get_redis`/`get_bot_from_api_key`) - the "bot API key OR JWT+permission"
composition below is specific to `messaging`'s semantics
(`MessagingPrincipal.bot_id` is only meaningful for scoping bots within this
module), the same reason `core/pagination.py` (`pagination_params`) also
deliberately lives outside `core/deps.py`. It reuses the public helpers from
`core.deps` (`get_bot_from_api_key`/`get_optional_jwt_payload`/
`resolve_user_from_payload`/`resolve_tenant_from_payload`/`check_permission`)
to avoid duplicating the same security logic.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    check_permission,
    get_bot_from_api_key,
    get_db,
    get_optional_jwt_payload,
    resolve_tenant_from_payload,
    resolve_user_from_payload,
)
from app.modules.auth.exceptions import TokenInvalidError
from app.modules.bots import Bot


@dataclass(frozen=True, slots=True)
class MessagingPrincipal:
    """Who authenticated the request to an externally-facing messaging

    endpoint (`POST /messages`, `GET /messages/{id}`, `.../status`) - two
    possibilities:
    - A dashboard user (JWT, `Authorization: Bearer`): `user_id` is set,
      `bot_id` is `None` - the regular role permission check has already
      happened here (`check_permission`); the "assigned bot" scoping for
      the MEMBER role is still checked separately in
      `messaging.service._assert_bot_access` as before (it needs to know
      the target `bot_id`, which only appears in the request body/message).
    - An external app (`X-Bot-Api-Key`): `bot_id` is set (the identity of
      the bot that owns the key), `user_id` is `None` - there's NO
      membership/role concept at all; `_assert_bot_access` treats
      `user_id=None` as "this credential is definitely that bot itself",
      and just matches the target `bot_id` via `restrict_to_bot_id`.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    bot_id: uuid.UUID | None


def require_permission_or_bot_api_key(
    permission: str,
) -> Callable[..., Awaitable[MessagingPrincipal]]:
    """Same as `core.deps.require_permission`, but also accepts a second

    path: a valid `X-Bot-Api-Key` header. A bot API key ALWAYS passes the
    permission check (there's no role concept for it - the bot's own
    identity is already the tightest possible scope, since a bot can only
    ever access itself via `MessagingPrincipal.bot_id`). If neither an API
    key nor a Bearer token is present at all, raises `TokenInvalidError`
    (401) - same as FastAPI's built-in `HTTPBearer(auto_error=True)` when a
    regular (single-auth) endpoint is called without an `Authorization` header.
    """

    async def _checker(
        session: AsyncSession = Depends(get_db),
        bot: Bot | None = Depends(get_bot_from_api_key),
        payload: dict[str, Any] | None = Depends(get_optional_jwt_payload),
    ) -> MessagingPrincipal:
        if bot is not None:
            return MessagingPrincipal(tenant_id=bot.tenant_id, user_id=None, bot_id=bot.id)

        if payload is None:
            raise TokenInvalidError()

        user = await resolve_user_from_payload(session, payload)
        tenant_id = resolve_tenant_from_payload(payload)
        await check_permission(session, user_id=user.id, tenant_id=tenant_id, permission=permission)
        return MessagingPrincipal(tenant_id=tenant_id, user_id=user.id, bot_id=None)

    return _checker
