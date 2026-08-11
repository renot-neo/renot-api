"""Unit tests for `app.modules.bots.service`.

Pure logic - the repository & Telegram API (`app.shared.telegram_client`)
are mocked, no real DB/network. Every external call to the Telegram Bot API
must be mocked in unit/integration tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.pagination import Page, PageParams
from app.modules.bots import service
from app.modules.bots.exceptions import (
    BotAlreadyRegisteredError,
    BotApiKeyInvalidError,
    BotAssignmentAlreadyExistsError,
    BotAssignmentNotFoundError,
    BotAssignmentUserNotMemberError,
    BotNotFoundError,
    BotTokenInvalidError,
    BotWebhookSetupFailedError,
)
from app.modules.bots.model import Bot, BotAssignment
from app.shared.telegram_client import TelegramAPIError

_ME_RESULT = {"id": 123456789, "is_bot": True, "username": "mybot", "first_name": "My Bot"}


def _bot(**overrides: object) -> Bot:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "name": "My Bot",
        "telegram_bot_id": 123456789,
        "username": "mybot",
        "token": "123456:dummy-token",
        "webhook_secret": "secret",
        "webhook_enabled": False,
        "api_key_hash": "hash",
        "api_key_prefix": "tgbm_live_abcd",
        "outbound_callback_url": None,
    }
    defaults.update(overrides)
    return Bot(**defaults)  # type: ignore[arg-type]


def _assignment(**overrides: object) -> BotAssignment:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "bot_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "deleted_at": None,
    }
    defaults.update(overrides)
    return BotAssignment(**defaults)  # type: ignore[arg-type]


def test_bot_token_last_four_derived_from_token() -> None:
    bot = _bot(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    assert bot.token_last_four == "ew11"
    assert bot.token_last_four == bot.token[-4:]


@pytest.mark.asyncio
async def test_register_bot_success_calls_get_me_and_set_webhook() -> None:
    created_bot = _bot()
    with (
        patch("app.modules.bots.service.get_me", AsyncMock(return_value=_ME_RESULT)),
        patch("app.modules.bots.service.set_webhook", AsyncMock(return_value=True)) as set_wh,
        patch("app.modules.bots.service.BotRepository") as repo_cls,
    ):
        repo = repo_cls.return_value
        repo.get_active_by_telegram_bot_id = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=created_bot)

        bot, api_key = await service.register_bot(
            AsyncMock(), tenant_id=uuid.uuid4(), name="My Bot", token="123456:dummy-token"
        )

        assert bot is created_bot
        assert api_key.startswith("tgbm_live_")
        create_kwargs = repo.create.call_args.kwargs
        assert create_kwargs["telegram_bot_id"] == 123456789
        assert create_kwargs["username"] == "mybot"
        assert create_kwargs["token"] == "123456:dummy-token"
        set_wh.assert_awaited_once()
        assert set_wh.await_args.kwargs["url"].endswith(f"/api/v1/webhooks/telegram/{bot.id}")


@pytest.mark.asyncio
async def test_register_bot_raises_when_token_invalid() -> None:
    with patch(
        "app.modules.bots.service.get_me",
        AsyncMock(side_effect=TelegramAPIError("Unauthorized", error_code=401)),
    ):
        with pytest.raises(BotTokenInvalidError):
            await service.register_bot(
                AsyncMock(), tenant_id=uuid.uuid4(), name="My Bot", token="bad-token"
            )


@pytest.mark.asyncio
async def test_register_bot_raises_when_already_registered() -> None:
    with (
        patch("app.modules.bots.service.get_me", AsyncMock(return_value=_ME_RESULT)),
        patch("app.modules.bots.service.BotRepository") as repo_cls,
    ):
        repo_cls.return_value.get_active_by_telegram_bot_id = AsyncMock(return_value=_bot())

        with pytest.raises(BotAlreadyRegisteredError):
            await service.register_bot(
                AsyncMock(), tenant_id=uuid.uuid4(), name="My Bot", token="123456:dummy-token"
            )


@pytest.mark.asyncio
async def test_register_bot_raises_when_set_webhook_fails() -> None:
    with (
        patch("app.modules.bots.service.get_me", AsyncMock(return_value=_ME_RESULT)),
        patch(
            "app.modules.bots.service.set_webhook",
            AsyncMock(side_effect=TelegramAPIError("Bad webhook URL")),
        ),
        patch("app.modules.bots.service.BotRepository") as repo_cls,
    ):
        repo = repo_cls.return_value
        repo.get_active_by_telegram_bot_id = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=_bot())

        with pytest.raises(BotWebhookSetupFailedError):
            await service.register_bot(
                AsyncMock(), tenant_id=uuid.uuid4(), name="My Bot", token="123456:dummy-token"
            )


@pytest.mark.asyncio
async def test_get_bot_raises_when_not_found() -> None:
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(BotNotFoundError):
            await service.get_bot(AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_list_bots_delegates_to_repository() -> None:
    bots = [_bot(), _bot()]
    params = PageParams()
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.list_active = AsyncMock(
            return_value=Page(items=bots, total=2, page=1, page_size=params.page_size)
        )

        result = await service.list_bots(AsyncMock(), tenant_id=uuid.uuid4(), page_params=params)

        assert result.items == bots


@pytest.mark.asyncio
async def test_update_bot_can_set_outbound_callback_url() -> None:
    existing = _bot(outbound_callback_url=None)
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        updated = await service.update_bot(
            AsyncMock(),
            tenant_id=uuid.uuid4(),
            bot_id=existing.id,
            outbound_callback_url="https://new.example.com/hook",
        )

        assert updated.outbound_callback_url == "https://new.example.com/hook"


@pytest.mark.asyncio
async def test_update_bot_can_clear_outbound_callback_url_with_empty_string() -> None:
    existing = _bot(outbound_callback_url="https://old.example.com/hook")
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        updated = await service.update_bot(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=existing.id, outbound_callback_url=""
        )

        assert updated.outbound_callback_url is None


@pytest.mark.asyncio
async def test_update_bot_only_changes_provided_fields() -> None:
    existing = _bot(name="Old Name", outbound_callback_url="https://old.example.com/hook")
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        updated = await service.update_bot(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=existing.id, name="New Name"
        )

        assert updated.name == "New Name"
        assert updated.outbound_callback_url == "https://old.example.com/hook"


@pytest.mark.asyncio
async def test_regenerate_api_key_replaces_hash_and_prefix() -> None:
    existing = _bot(api_key_hash="old-hash", api_key_prefix="tgbm_live_oldp")
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        bot, api_key = await service.regenerate_api_key(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=existing.id
        )

        assert bot.api_key_hash != "old-hash"
        assert bot.api_key_prefix != "tgbm_live_oldp"
        assert api_key.startswith("tgbm_live_")


@pytest.mark.asyncio
async def test_update_subscription_policy_toggles_webhook_enabled() -> None:
    existing = _bot(webhook_enabled=False)
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        updated = await service.update_subscription_policy(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=existing.id, webhook_enabled=True
        )

        assert updated.webhook_enabled is True


@pytest.mark.asyncio
async def test_delete_bot_calls_soft_delete() -> None:
    existing = _bot()
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_active = AsyncMock(return_value=existing)
        repo.soft_delete = AsyncMock()

        await service.delete_bot(AsyncMock(), tenant_id=uuid.uuid4(), bot_id=existing.id)

        repo.soft_delete.assert_awaited_once_with(existing)


@pytest.mark.asyncio
async def test_get_bot_token_returns_stored_token() -> None:
    existing = _bot(token="123456:real-token")
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        token = await service.get_bot_token(AsyncMock(), tenant_id=uuid.uuid4(), bot_id=existing.id)

        assert token == "123456:real-token"


@pytest.mark.asyncio
async def test_get_bot_for_webhook_returns_bot_without_tenant_filter() -> None:
    existing = _bot()
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_active_by_id = AsyncMock(return_value=existing)

        bot = await service.get_bot_for_webhook(AsyncMock(), bot_id=existing.id)

        assert bot is existing
        repo.get_active_by_id.assert_awaited_once_with(existing.id)


@pytest.mark.asyncio
async def test_get_bot_for_webhook_raises_when_not_found() -> None:
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active_by_id = AsyncMock(return_value=None)

        with pytest.raises(BotNotFoundError):
            await service.get_bot_for_webhook(AsyncMock(), bot_id=uuid.uuid4())


# --- get_bot_by_api_key (dual-auth for external messaging) ---


@pytest.mark.asyncio
async def test_get_bot_by_api_key_returns_bot_for_matching_hash() -> None:
    existing = _bot()
    with (
        patch("app.modules.bots.service.BotRepository") as repo_cls,
        patch("app.modules.bots.service._hash_api_key", return_value="hashed") as mock_hash,
    ):
        repo = repo_cls.return_value
        repo.get_active_by_api_key_hash = AsyncMock(return_value=existing)

        bot = await service.get_bot_by_api_key(AsyncMock(), "tgbm_live_plaintext")

        assert bot is existing
        mock_hash.assert_called_once_with("tgbm_live_plaintext")
        repo.get_active_by_api_key_hash.assert_awaited_once_with("hashed")


@pytest.mark.asyncio
async def test_get_bot_by_api_key_raises_when_no_match() -> None:
    with patch("app.modules.bots.service.BotRepository") as repo_cls:
        repo_cls.return_value.get_active_by_api_key_hash = AsyncMock(return_value=None)

        with pytest.raises(BotApiKeyInvalidError):
            await service.get_bot_by_api_key(AsyncMock(), "tgbm_live_wrong")


# --- BotAssignment ("MEMBER can only access assigned bots") ---


@pytest.mark.asyncio
async def test_assign_bot_creates_new_row_when_none_exists() -> None:
    bot = _bot()
    created = _assignment(bot_id=bot.id)
    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.get_membership", AsyncMock(return_value=object())),
        patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls,
    ):
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=bot)
        assign_repo = assign_repo_cls.return_value
        assign_repo.get_with_deleted = AsyncMock(return_value=None)
        assign_repo.create = AsyncMock(return_value=created)

        result = await service.assign_bot(
            AsyncMock(), tenant_id=bot.tenant_id, bot_id=bot.id, user_id=created.user_id
        )

        assert result is created
        assign_repo.create.assert_awaited_once_with(
            tenant_id=bot.tenant_id, bot_id=bot.id, user_id=created.user_id
        )


@pytest.mark.asyncio
async def test_assign_bot_reactivates_soft_deleted_row_instead_of_creating() -> None:
    bot = _bot()
    existing = _assignment(bot_id=bot.id, deleted_at=datetime.now(UTC))
    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.get_membership", AsyncMock(return_value=object())),
        patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls,
    ):
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=bot)
        assign_repo = assign_repo_cls.return_value
        assign_repo.get_with_deleted = AsyncMock(return_value=existing)
        assign_repo.create = AsyncMock()

        result = await service.assign_bot(
            AsyncMock(), tenant_id=bot.tenant_id, bot_id=bot.id, user_id=existing.user_id
        )

        assert result is existing
        assert existing.deleted_at is None
        assign_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_assign_bot_raises_when_already_actively_assigned() -> None:
    bot = _bot()
    existing = _assignment(bot_id=bot.id, deleted_at=None)
    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.get_membership", AsyncMock(return_value=object())),
        patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls,
    ):
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=bot)
        assign_repo_cls.return_value.get_with_deleted = AsyncMock(return_value=existing)

        with pytest.raises(BotAssignmentAlreadyExistsError):
            await service.assign_bot(
                AsyncMock(), tenant_id=bot.tenant_id, bot_id=bot.id, user_id=existing.user_id
            )


@pytest.mark.asyncio
async def test_assign_bot_raises_when_user_not_org_member() -> None:
    bot = _bot()
    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.get_membership", AsyncMock(return_value=None)),
    ):
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=bot)

        with pytest.raises(BotAssignmentUserNotMemberError):
            await service.assign_bot(
                AsyncMock(), tenant_id=bot.tenant_id, bot_id=bot.id, user_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_assign_bot_propagates_bot_not_found() -> None:
    with patch("app.modules.bots.service.BotRepository") as bot_repo_cls:
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(BotNotFoundError):
            await service.assign_bot(
                AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), user_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_unassign_bot_soft_deletes_existing_assignment() -> None:
    bot = _bot()
    existing = _assignment(bot_id=bot.id)
    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls,
    ):
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=bot)
        assign_repo = assign_repo_cls.return_value
        assign_repo.get_active = AsyncMock(return_value=existing)
        assign_repo.soft_delete = AsyncMock()

        await service.unassign_bot(
            AsyncMock(), tenant_id=bot.tenant_id, bot_id=bot.id, user_id=existing.user_id
        )

        assign_repo.soft_delete.assert_awaited_once_with(existing)


@pytest.mark.asyncio
async def test_unassign_bot_raises_when_not_assigned() -> None:
    bot = _bot()
    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls,
    ):
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=bot)
        assign_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(BotAssignmentNotFoundError):
            await service.unassign_bot(
                AsyncMock(), tenant_id=bot.tenant_id, bot_id=bot.id, user_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_list_bot_assignments_delegates_to_repository() -> None:
    bot = _bot()
    assignments = [_assignment(bot_id=bot.id), _assignment(bot_id=bot.id)]
    params = PageParams()
    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls,
    ):
        bot_repo_cls.return_value.get_active = AsyncMock(return_value=bot)
        assign_repo_cls.return_value.list_active_for_bot = AsyncMock(
            return_value=Page(items=assignments, total=2, page=1, page_size=params.page_size)
        )

        result = await service.list_bot_assignments(
            AsyncMock(), tenant_id=bot.tenant_id, bot_id=bot.id, page_params=params
        )

        assert result.items == assignments


@pytest.mark.asyncio
async def test_is_assigned_returns_true_when_active_assignment_exists() -> None:
    with patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls:
        assign_repo_cls.return_value.get_active = AsyncMock(return_value=_assignment())

        result = await service.is_assigned(
            session=AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), user_id=uuid.uuid4()
        )

        assert result is True


@pytest.mark.asyncio
async def test_is_assigned_returns_false_when_no_active_assignment() -> None:
    with patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls:
        assign_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        result = await service.is_assigned(
            session=AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), user_id=uuid.uuid4()
        )

        assert result is False


@pytest.mark.asyncio
async def test_cascade_delete_for_organization_soft_deletes_all_bots_and_assignments() -> None:
    """Called by `modules/organizations.delete_organization` (the

    organization-delete cascade) - every active bot + assignment for this
    tenant must be soft-deleted, not just one bot like `delete_bot`.
    """
    tenant_id = uuid.uuid4()
    bot_one, bot_two = _bot(), _bot()
    assignment_one, assignment_two = _assignment(), _assignment()

    with (
        patch("app.modules.bots.service.BotRepository") as bot_repo_cls,
        patch("app.modules.bots.service.BotAssignmentRepository") as assign_repo_cls,
    ):
        bot_repo = bot_repo_cls.return_value
        bot_repo.list_all_active = AsyncMock(return_value=[bot_one, bot_two])
        bot_repo.soft_delete = AsyncMock()

        assign_repo = assign_repo_cls.return_value
        assign_repo.list_all_active_for_tenant = AsyncMock(
            return_value=[assignment_one, assignment_two]
        )
        assign_repo.soft_delete = AsyncMock()

        await service.cascade_delete_for_organization(AsyncMock(), tenant_id=tenant_id)

        assert bot_repo.soft_delete.await_count == 2
        assert assign_repo.soft_delete.await_count == 2
        bot_repo.list_all_active.assert_awaited_once_with(tenant_id=tenant_id)
        assign_repo.list_all_active_for_tenant.assert_awaited_once_with(tenant_id=tenant_id)
