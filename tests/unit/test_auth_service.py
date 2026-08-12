"""Unit tests for `app.modules.auth.service`.

Pure logic - the repository is mocked, no real DB/network.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.auth import service
from app.modules.auth.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    RefreshTokenInvalidError,
    UserInactiveError,
)
from app.modules.auth.model import RefreshToken, User


def _make_user(**overrides: object) -> User:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "email": "user@example.com",
        "password_hash": service.hash_password("correct-password"),
        "full_name": "Test User",
        "is_active": True,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_register_raises_when_email_already_taken() -> None:
    with patch("app.modules.auth.service.UserRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_by_email = AsyncMock(return_value=_make_user())

        with pytest.raises(EmailAlreadyRegisteredError):
            await service.register(
                AsyncMock(), email="user@example.com", password="x", full_name="X"
            )


@pytest.mark.asyncio
async def test_register_hashes_password_and_creates_user() -> None:
    with patch("app.modules.auth.service.UserRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_by_email = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=_make_user())

        await service.register(
            AsyncMock(), email="new@example.com", password="plaintext-pw", full_name="New"
        )

        _, kwargs = repo.create.call_args
        assert kwargs["password_hash"] != "plaintext-pw"
        assert service.verify_password("plaintext-pw", kwargs["password_hash"])


@pytest.mark.asyncio
async def test_authenticate_rejects_wrong_password() -> None:
    with patch("app.modules.auth.service.UserRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_by_email = AsyncMock(return_value=_make_user())

        with pytest.raises(InvalidCredentialsError):
            await service.authenticate(AsyncMock(), email="user@example.com", password="wrong")


@pytest.mark.asyncio
async def test_authenticate_rejects_unknown_email() -> None:
    with patch("app.modules.auth.service.UserRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_by_email = AsyncMock(return_value=None)

        with pytest.raises(InvalidCredentialsError):
            await service.authenticate(AsyncMock(), email="nobody@example.com", password="x")


@pytest.mark.asyncio
async def test_authenticate_rejects_inactive_user() -> None:
    user = _make_user(is_active=False)
    with patch("app.modules.auth.service.UserRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_by_email = AsyncMock(return_value=user)

        with pytest.raises(UserInactiveError):
            await service.authenticate(
                AsyncMock(), email="user@example.com", password="correct-password"
            )


@pytest.mark.asyncio
async def test_login_issues_token_pair_without_tenant_context() -> None:
    user = _make_user()
    with (
        patch("app.modules.auth.service.UserRepository") as user_repo_cls,
        patch("app.modules.auth.service.RefreshTokenRepository") as token_repo_cls,
    ):
        user_repo_cls.return_value.get_by_email = AsyncMock(return_value=user)
        token_repo_cls.return_value.create = AsyncMock()

        tokens = await service.login(
            AsyncMock(), email="user@example.com", password="correct-password"
        )

        assert tokens.token_type == "bearer"
        _, kwargs = token_repo_cls.return_value.create.call_args
        assert kwargs["tenant_id"] is None


@pytest.mark.asyncio
async def test_refresh_rejects_expired_token() -> None:
    user = _make_user()
    refresh_value = service.create_refresh_token(subject=str(user.id))
    expired_row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        tenant_id=None,
        token_hash=service._hash_token(refresh_value),
        expires_at=datetime.now(UTC) - timedelta(days=1),
        revoked_at=None,
    )
    with patch("app.modules.auth.service.RefreshTokenRepository") as token_repo_cls:
        token_repo_cls.return_value.get_active_by_hash = AsyncMock(return_value=expired_row)

        with pytest.raises(RefreshTokenInvalidError):
            await service.refresh(AsyncMock(), refresh_token=refresh_value)


@pytest.mark.asyncio
async def test_refresh_rejects_unknown_token() -> None:
    with patch("app.modules.auth.service.RefreshTokenRepository") as token_repo_cls:
        token_repo_cls.return_value.get_active_by_hash = AsyncMock(return_value=None)

        with pytest.raises(RefreshTokenInvalidError):
            await service.refresh(AsyncMock(), refresh_token="not-even-a-jwt")


@pytest.mark.asyncio
async def test_refresh_rejects_access_token_used_as_refresh_token() -> None:
    access_value = service.create_access_token(subject=str(uuid.uuid4()), tenant_id=None)

    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(AsyncMock(), refresh_token=access_value)


@pytest.mark.asyncio
async def test_refresh_rejects_when_owning_user_is_inactive() -> None:
    """The stored refresh token itself is still valid/unexpired, but the

    user it belongs to was deactivated in the meantime - must not rotate a
    new token pair for a deactivated account.
    """
    user = _make_user(is_active=False)
    refresh_value = service.create_refresh_token(subject=str(user.id))
    stored_row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        tenant_id=None,
        token_hash=service._hash_token(refresh_value),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        revoked_at=None,
    )
    with (
        patch("app.modules.auth.service.RefreshTokenRepository") as token_repo_cls,
        patch("app.modules.auth.service.UserRepository") as user_repo_cls,
    ):
        token_repo_cls.return_value.get_active_by_hash = AsyncMock(return_value=stored_row)
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)

        with pytest.raises(RefreshTokenInvalidError):
            await service.refresh(AsyncMock(), refresh_token=refresh_value)


@pytest.mark.asyncio
async def test_refresh_preserves_tenant_context_and_rotates_token() -> None:
    user = _make_user()
    tenant_id = uuid.uuid4()
    refresh_value = service.create_refresh_token(subject=str(user.id))
    stored_row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        tenant_id=tenant_id,
        token_hash=service._hash_token(refresh_value),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        revoked_at=None,
    )
    with (
        patch("app.modules.auth.service.RefreshTokenRepository") as token_repo_cls,
        patch("app.modules.auth.service.UserRepository") as user_repo_cls,
    ):
        token_repo = token_repo_cls.return_value
        token_repo.get_active_by_hash = AsyncMock(return_value=stored_row)
        token_repo.revoke = AsyncMock()
        token_repo.create = AsyncMock()
        user_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)

        tokens = await service.refresh(AsyncMock(), refresh_token=refresh_value)

        token_repo.revoke.assert_awaited_once_with(stored_row)
        _, kwargs = token_repo.create.call_args
        assert kwargs["tenant_id"] == tenant_id
        assert tokens.access_token


@pytest.mark.asyncio
async def test_switch_organization_rejects_non_member() -> None:
    user = _make_user()
    with patch("app.modules.organizations.get_membership", AsyncMock(return_value=None)):
        from app.modules.organizations.exceptions import NotOrganizationMemberError

        with pytest.raises(NotOrganizationMemberError):
            await service.switch_organization(AsyncMock(), user=user, organization_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_revoke_tenant_refresh_tokens_delegates_to_repository() -> None:
    """Called by `modules/organizations.delete_organization` (the

    organization-delete cascade) via the public `app.modules.auth` interface.
    """
    tenant_id = uuid.uuid4()
    with patch("app.modules.auth.service.RefreshTokenRepository") as token_repo_cls:
        token_repo_cls.return_value.revoke_all_for_tenant = AsyncMock()

        await service.revoke_tenant_refresh_tokens(AsyncMock(), tenant_id=tenant_id)

        token_repo_cls.return_value.revoke_all_for_tenant.assert_awaited_once_with(
            tenant_id=tenant_id
        )
