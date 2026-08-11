"""Unit tests for `app.modules.messaging.deps` (dual-auth `X-Bot-Api-Key`).

Pure logic - every `core.deps` helper reused here is mocked, no real DB/network.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.modules.auth.exceptions import TokenInvalidError
from app.modules.messaging.deps import MessagingPrincipal, require_permission_or_bot_api_key


@pytest.mark.asyncio
async def test_returns_bot_principal_when_api_key_present() -> None:
    bot = Mock(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    checker = require_permission_or_bot_api_key("message:send")

    with (
        patch("app.modules.messaging.deps.resolve_user_from_payload") as mock_resolve_user,
        patch("app.modules.messaging.deps.check_permission") as mock_check_permission,
    ):
        principal = await checker(session=AsyncMock(), bot=bot, payload={"sub": "irrelevant"})

    assert principal == MessagingPrincipal(tenant_id=bot.tenant_id, user_id=None, bot_id=bot.id)
    # A bot API key ALWAYS passes without any permission/JWT check at all -
    # even if (hypothetically) `payload` were also present, `bot` wins first.
    mock_resolve_user.assert_not_called()
    mock_check_permission.assert_not_called()


@pytest.mark.asyncio
async def test_raises_token_invalid_when_no_api_key_and_no_payload() -> None:
    checker = require_permission_or_bot_api_key("message:send")

    with pytest.raises(TokenInvalidError):
        await checker(session=AsyncMock(), bot=None, payload=None)


@pytest.mark.asyncio
async def test_falls_back_to_jwt_permission_check_when_no_api_key() -> None:
    user = Mock(id=uuid.uuid4())
    tenant_id = uuid.uuid4()
    payload = {"sub": str(user.id), "tenant_id": str(tenant_id)}
    checker = require_permission_or_bot_api_key("log:view")

    with (
        patch(
            "app.modules.messaging.deps.resolve_user_from_payload",
            AsyncMock(return_value=user),
        ) as mock_resolve_user,
        patch(
            "app.modules.messaging.deps.resolve_tenant_from_payload", return_value=tenant_id
        ) as mock_resolve_tenant,
        patch("app.modules.messaging.deps.check_permission", AsyncMock()) as mock_check_permission,
    ):
        session = AsyncMock()
        principal = await checker(session=session, bot=None, payload=payload)

    assert principal == MessagingPrincipal(tenant_id=tenant_id, user_id=user.id, bot_id=None)
    mock_resolve_user.assert_awaited_once_with(session, payload)
    mock_resolve_tenant.assert_called_once_with(payload)
    mock_check_permission.assert_awaited_once_with(
        session, user_id=user.id, tenant_id=tenant_id, permission="log:view"
    )
