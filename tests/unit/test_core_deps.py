"""Unit tests for `app.core.deps` - specifically the pure-logic helpers

that can be tested without a real DB/HTTP
(`resolve_user_from_payload`/`resolve_tenant_from_payload`/
`check_permission`/`get_optional_jwt_payload`/`get_bot_from_api_key`). This
file historically had no direct unit tests (everything was verified through
`tests/integration`, which runs a real FastAPI DI stack) - added once the
`X-Bot-Api-Key` dual-auth work added several new generic helpers here that
are worth testing directly, not just transitively through an HTTP endpoint.

`get_db`/`get_redis`/`get_current_user`/`get_current_tenant`/`require_permission`
are deliberately NOT re-tested here - already thoroughly verified through
`tests/integration` (a real FastAPI DI stack) and `test_core_rate_limit.py`
(for `get_redis`'s loop-safety); no need to duplicate that.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core import deps
from app.core.security import JWTError, create_refresh_token
from app.modules.auth.exceptions import (
    NoActiveOrganizationError,
    TokenInvalidError,
    UserInactiveError,
)
from app.modules.organizations.exceptions import (
    InsufficientPermissionError,
    NotOrganizationMemberError,
    OrganizationNotFoundError,
)

# --- resolve_user_from_payload ---


@pytest.mark.asyncio
async def test_resolve_user_from_payload_returns_active_user() -> None:
    user = Mock(is_active=True)
    user_id = uuid.uuid4()
    session = AsyncMock()
    with patch("app.core.deps.get_user_by_id", AsyncMock(return_value=user)) as mock_get_user:
        result = await deps.resolve_user_from_payload(session, {"sub": str(user_id)})

    assert result is user
    mock_get_user.assert_awaited_once_with(session, user_id)


@pytest.mark.asyncio
async def test_resolve_user_from_payload_raises_when_sub_missing() -> None:
    with pytest.raises(TokenInvalidError):
        await deps.resolve_user_from_payload(AsyncMock(), {})


@pytest.mark.asyncio
async def test_resolve_user_from_payload_raises_when_sub_not_a_uuid() -> None:
    with pytest.raises(TokenInvalidError):
        await deps.resolve_user_from_payload(AsyncMock(), {"sub": "not-a-uuid"})


@pytest.mark.asyncio
async def test_resolve_user_from_payload_raises_when_user_not_found() -> None:
    with patch("app.core.deps.get_user_by_id", AsyncMock(return_value=None)):
        with pytest.raises(TokenInvalidError):
            await deps.resolve_user_from_payload(AsyncMock(), {"sub": str(uuid.uuid4())})


@pytest.mark.asyncio
async def test_resolve_user_from_payload_raises_when_user_inactive() -> None:
    with patch("app.core.deps.get_user_by_id", AsyncMock(return_value=Mock(is_active=False))):
        with pytest.raises(UserInactiveError):
            await deps.resolve_user_from_payload(AsyncMock(), {"sub": str(uuid.uuid4())})


# --- resolve_tenant_from_payload ---


def test_resolve_tenant_from_payload_returns_uuid() -> None:
    tenant_id = uuid.uuid4()
    assert deps.resolve_tenant_from_payload({"tenant_id": str(tenant_id)}) == tenant_id


@pytest.mark.parametrize("payload", [{}, {"tenant_id": None}, {"tenant_id": ""}])
def test_resolve_tenant_from_payload_raises_when_missing(payload: dict[str, object]) -> None:
    with pytest.raises(NoActiveOrganizationError):
        deps.resolve_tenant_from_payload(payload)


# --- check_permission ---


@pytest.mark.asyncio
async def test_check_permission_raises_when_not_a_member() -> None:
    with patch("app.core.deps.get_membership", AsyncMock(return_value=None)):
        with pytest.raises(NotOrganizationMemberError):
            await deps.check_permission(
                AsyncMock(), user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), permission="bot:view"
            )


@pytest.mark.asyncio
async def test_check_permission_raises_when_organization_inactive() -> None:
    with (
        patch("app.core.deps.get_membership", AsyncMock(return_value=Mock())),
        patch("app.core.deps.is_organization_active", AsyncMock(return_value=False)),
    ):
        with pytest.raises(OrganizationNotFoundError):
            await deps.check_permission(
                AsyncMock(), user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), permission="bot:view"
            )


@pytest.mark.asyncio
async def test_check_permission_raises_when_role_lacks_permission() -> None:
    with (
        patch("app.core.deps.get_membership", AsyncMock(return_value=Mock())),
        patch("app.core.deps.is_organization_active", AsyncMock(return_value=True)),
        patch("app.core.deps.has_permission", return_value=False),
    ):
        with pytest.raises(InsufficientPermissionError):
            await deps.check_permission(
                AsyncMock(), user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), permission="bot:manage"
            )


@pytest.mark.asyncio
async def test_check_permission_passes_when_all_checks_ok() -> None:
    with (
        patch("app.core.deps.get_membership", AsyncMock(return_value=Mock())),
        patch("app.core.deps.is_organization_active", AsyncMock(return_value=True)),
        patch("app.core.deps.has_permission", return_value=True),
    ):
        await deps.check_permission(
            AsyncMock(), user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), permission="bot:view"
        )


# --- _decode_bearer_token ---
# Historically untested directly (see the module docstring) - covered
# indirectly via `tests/integration`'s real FastAPI DI stack, but no
# integration test ever sends a genuinely malformed/wrong-type bearer
# token (only "no credentials at all", which FastAPI's own
# `HTTPBearer(auto_error=True)` rejects before this function's body ever
# runs) - added directly here instead of via HTTP for that reason.


@pytest.mark.asyncio
async def test_decode_bearer_token_raises_when_token_undecodable() -> None:
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-real-jwt")

    with pytest.raises(TokenInvalidError):
        await deps._decode_bearer_token(credentials)


@pytest.mark.asyncio
async def test_decode_bearer_token_raises_when_token_type_is_not_access() -> None:
    refresh_token = create_refresh_token(subject=str(uuid.uuid4()))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=refresh_token)

    with pytest.raises(TokenInvalidError):
        await deps._decode_bearer_token(credentials)


# --- get_optional_jwt_payload ---


@pytest.mark.asyncio
async def test_get_optional_jwt_payload_returns_none_when_no_credentials() -> None:
    assert await deps.get_optional_jwt_payload(credentials=None) is None


@pytest.mark.asyncio
async def test_get_optional_jwt_payload_returns_payload_for_valid_access_token() -> None:
    payload = {"sub": "user-1", "type": "access"}
    with patch("app.core.deps.decode_token", return_value=payload):
        result = await deps.get_optional_jwt_payload(credentials=Mock(credentials="token"))
    assert result == payload


@pytest.mark.asyncio
async def test_get_optional_jwt_payload_raises_when_token_undecodable() -> None:
    with patch("app.core.deps.decode_token", side_effect=JWTError("bad token")):
        with pytest.raises(TokenInvalidError):
            await deps.get_optional_jwt_payload(credentials=Mock(credentials="garbage"))


@pytest.mark.asyncio
async def test_get_optional_jwt_payload_raises_when_not_access_type() -> None:
    with patch("app.core.deps.decode_token", return_value={"sub": "user-1", "type": "refresh"}):
        with pytest.raises(TokenInvalidError):
            await deps.get_optional_jwt_payload(credentials=Mock(credentials="token"))


# --- get_bot_from_api_key ---


@pytest.mark.asyncio
async def test_get_bot_from_api_key_returns_none_when_no_header() -> None:
    assert await deps.get_bot_from_api_key(api_key=None, session=AsyncMock()) is None


@pytest.mark.asyncio
async def test_get_bot_from_api_key_delegates_to_bots_module_when_header_present() -> None:
    bot = Mock()
    session = AsyncMock()
    with patch("app.core.deps.get_bot_by_api_key", AsyncMock(return_value=bot)) as mock_get_bot:
        result = await deps.get_bot_from_api_key(api_key="tgbm_live_x", session=session)

    assert result is bot
    mock_get_bot.assert_awaited_once_with(session, "tgbm_live_x")
