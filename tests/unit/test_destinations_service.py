"""Unit tests for `app.modules.destinations.service`.

Pure logic - the repository & `modules/bots`'s service interface (`get_bot`)
are mocked, no real DB.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.pagination import Page, PageParams
from app.modules.bots.exceptions import BotNotFoundError
from app.modules.destinations import service
from app.modules.destinations.exceptions import (
    DestinationAlreadyExistsError,
    DestinationNotFoundError,
    SubscriptionNotFoundError,
)
from app.modules.destinations.model import (
    BotDestinationSubscription,
    Destination,
    DestinationType,
    SubscriptionStatus,
)


def _destination(**overrides: object) -> Destination:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "type": DestinationType.GROUP,
        "chat_id": -100123456789,
        "thread_id": None,
        "title": "My Group",
    }
    defaults.update(overrides)
    return Destination(**defaults)  # type: ignore[arg-type]


def _subscription(**overrides: object) -> BotDestinationSubscription:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "bot_id": uuid.uuid4(),
        "destination_id": uuid.uuid4(),
        "status": SubscriptionStatus.ACTIVE,
    }
    defaults.update(overrides)
    return BotDestinationSubscription(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_destination_success_creates_destination_and_active_subscription() -> None:
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    created = _destination(tenant_id=tenant_id)

    with (
        patch("app.modules.destinations.service.get_bot", AsyncMock(return_value=object())),
        patch("app.modules.destinations.service.DestinationRepository") as dest_repo_cls,
        patch("app.modules.destinations.service.SubscriptionRepository") as sub_repo_cls,
    ):
        dest_repo = dest_repo_cls.return_value
        dest_repo.get_active_by_chat = AsyncMock(return_value=None)
        dest_repo.create = AsyncMock(return_value=created)
        sub_repo = sub_repo_cls.return_value
        sub_repo.create = AsyncMock(return_value=_subscription())

        destination = await service.create_destination(
            AsyncMock(),
            tenant_id=tenant_id,
            bot_id=bot_id,
            type=DestinationType.GROUP,
            chat_id=-100123456789,
            thread_id=None,
            title="My Group",
        )

        assert destination is created
        sub_repo.create.assert_awaited_once_with(
            tenant_id=tenant_id, bot_id=bot_id, destination_id=created.id
        )


@pytest.mark.asyncio
async def test_create_destination_propagates_bot_not_found() -> None:
    with patch(
        "app.modules.destinations.service.get_bot", AsyncMock(side_effect=BotNotFoundError())
    ):
        with pytest.raises(BotNotFoundError):
            await service.create_destination(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                bot_id=uuid.uuid4(),
                type=DestinationType.GROUP,
                chat_id=123,
                thread_id=None,
                title="Group",
            )


@pytest.mark.asyncio
async def test_create_destination_raises_when_chat_already_registered() -> None:
    with (
        patch("app.modules.destinations.service.get_bot", AsyncMock(return_value=object())),
        patch("app.modules.destinations.service.DestinationRepository") as dest_repo_cls,
    ):
        dest_repo_cls.return_value.get_active_by_chat = AsyncMock(return_value=_destination())

        with pytest.raises(DestinationAlreadyExistsError):
            await service.create_destination(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                bot_id=uuid.uuid4(),
                type=DestinationType.GROUP,
                chat_id=123,
                thread_id=None,
                title="Group",
            )


@pytest.mark.asyncio
async def test_get_destination_raises_when_not_found() -> None:
    with patch("app.modules.destinations.service.DestinationRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(DestinationNotFoundError):
            await service.get_destination(
                AsyncMock(), tenant_id=uuid.uuid4(), destination_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_list_destinations_delegates_to_repository() -> None:
    destinations = [_destination(), _destination()]
    params = PageParams()
    with patch("app.modules.destinations.service.DestinationRepository") as repo_cls:
        repo_cls.return_value.list_active = AsyncMock(
            return_value=Page(items=destinations, total=2, page=1, page_size=params.page_size)
        )

        result = await service.list_destinations(
            AsyncMock(), tenant_id=uuid.uuid4(), page_params=params
        )

        assert result.items == destinations


@pytest.mark.asyncio
async def test_list_destinations_for_bot_pairs_destination_with_subscription() -> None:
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    destination = _destination(tenant_id=tenant_id)
    subscription = _subscription(tenant_id=tenant_id, bot_id=bot_id, destination_id=destination.id)
    params = PageParams()

    with (
        patch("app.modules.destinations.service.get_bot", AsyncMock(return_value=object())),
        patch("app.modules.destinations.service.SubscriptionRepository") as sub_repo_cls,
        patch("app.modules.destinations.service.DestinationRepository") as dest_repo_cls,
    ):
        sub_repo_cls.return_value.list_active_for_bot = AsyncMock(
            return_value=Page(items=[subscription], total=1, page=1, page_size=params.page_size)
        )
        dest_repo_cls.return_value.get_active = AsyncMock(return_value=destination)

        result = await service.list_destinations_for_bot(
            AsyncMock(), tenant_id=tenant_id, bot_id=bot_id, page_params=params
        )

        assert result.items == [(destination, subscription)]


@pytest.mark.asyncio
async def test_list_destinations_for_bot_propagates_bot_not_found() -> None:
    with patch(
        "app.modules.destinations.service.get_bot", AsyncMock(side_effect=BotNotFoundError())
    ):
        with pytest.raises(BotNotFoundError):
            await service.list_destinations_for_bot(
                AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), page_params=PageParams()
            )


@pytest.mark.asyncio
async def test_update_destination_only_changes_title() -> None:
    existing = _destination(title="Old Title")
    with patch("app.modules.destinations.service.DestinationRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        updated = await service.update_destination(
            AsyncMock(), tenant_id=uuid.uuid4(), destination_id=existing.id, title="New Title"
        )

        assert updated.title == "New Title"


@pytest.mark.asyncio
async def test_update_destination_leaves_title_unchanged_when_omitted() -> None:
    existing = _destination(title="Old Title")
    with patch("app.modules.destinations.service.DestinationRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        updated = await service.update_destination(
            AsyncMock(), tenant_id=uuid.uuid4(), destination_id=existing.id
        )

        assert updated.title == "Old Title"


@pytest.mark.asyncio
async def test_delete_destination_cascades_soft_delete_to_subscriptions() -> None:
    existing = _destination()
    subscriptions = [
        _subscription(destination_id=existing.id),
        _subscription(destination_id=existing.id),
    ]

    with (
        patch("app.modules.destinations.service.DestinationRepository") as dest_repo_cls,
        patch("app.modules.destinations.service.SubscriptionRepository") as sub_repo_cls,
    ):
        dest_repo = dest_repo_cls.return_value
        dest_repo.get_active = AsyncMock(return_value=existing)
        dest_repo.soft_delete = AsyncMock()
        sub_repo = sub_repo_cls.return_value
        sub_repo.list_active_for_destination = AsyncMock(return_value=subscriptions)
        sub_repo.soft_delete = AsyncMock()

        await service.delete_destination(
            AsyncMock(), tenant_id=uuid.uuid4(), destination_id=existing.id
        )

        assert sub_repo.soft_delete.await_count == 2
        dest_repo.soft_delete.assert_awaited_once_with(existing)


@pytest.mark.asyncio
async def test_update_subscription_status_success() -> None:
    tenant_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    subscription = _subscription(
        tenant_id=tenant_id,
        bot_id=bot_id,
        destination_id=destination_id,
        status=SubscriptionStatus.ACTIVE,
    )

    with (
        patch("app.modules.destinations.service.get_destination", AsyncMock(return_value=object())),
        patch("app.modules.destinations.service.get_bot", AsyncMock(return_value=object())),
        patch("app.modules.destinations.service.SubscriptionRepository") as sub_repo_cls,
    ):
        sub_repo_cls.return_value.get_active = AsyncMock(return_value=subscription)

        updated = await service.update_subscription_status(
            AsyncMock(),
            tenant_id=tenant_id,
            destination_id=destination_id,
            bot_id=bot_id,
            status=SubscriptionStatus.UNSUBSCRIBED,
        )

        assert updated.status == SubscriptionStatus.UNSUBSCRIBED


@pytest.mark.asyncio
async def test_update_subscription_status_raises_when_no_subscription() -> None:
    with (
        patch("app.modules.destinations.service.get_destination", AsyncMock(return_value=object())),
        patch("app.modules.destinations.service.get_bot", AsyncMock(return_value=object())),
        patch("app.modules.destinations.service.SubscriptionRepository") as sub_repo_cls,
    ):
        sub_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(SubscriptionNotFoundError):
            await service.update_subscription_status(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                destination_id=uuid.uuid4(),
                bot_id=uuid.uuid4(),
                status=SubscriptionStatus.UNSUBSCRIBED,
            )


@pytest.mark.asyncio
async def test_get_or_create_destination_returns_existing_without_creating() -> None:
    existing = _destination(title="Existing")
    with patch("app.modules.destinations.service.DestinationRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_active_by_chat = AsyncMock(return_value=existing)
        repo.create = AsyncMock()

        result = await service.get_or_create_destination(
            AsyncMock(),
            tenant_id=uuid.uuid4(),
            type=DestinationType.GROUP,
            chat_id=existing.chat_id,
            thread_id=None,
            title="New Title From Telegram",
        )

        assert result is existing
        repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_destination_creates_when_missing() -> None:
    created = _destination()
    with patch("app.modules.destinations.service.DestinationRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_active_by_chat = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=created)

        result = await service.get_or_create_destination(
            AsyncMock(),
            tenant_id=uuid.uuid4(),
            type=DestinationType.PERSONAL,
            chat_id=123,
            thread_id=None,
            title="Someone",
        )

        assert result is created
        repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_activate_subscription_creates_when_none_exists() -> None:
    created = _subscription(status=SubscriptionStatus.ACTIVE)
    with patch("app.modules.destinations.service.SubscriptionRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_active = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=created)

        result = await service.activate_subscription(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), destination_id=uuid.uuid4()
        )

        assert result is created
        repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_activate_subscription_reactivates_unsubscribed() -> None:
    existing = _subscription(status=SubscriptionStatus.UNSUBSCRIBED)
    with patch("app.modules.destinations.service.SubscriptionRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        result = await service.activate_subscription(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), destination_id=uuid.uuid4()
        )

        assert result.status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_activate_subscription_does_not_override_blocked_by_admin() -> None:
    existing = _subscription(status=SubscriptionStatus.BLOCKED_BY_ADMIN)
    with patch("app.modules.destinations.service.SubscriptionRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        result = await service.activate_subscription(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), destination_id=uuid.uuid4()
        )

        assert result.status == SubscriptionStatus.BLOCKED_BY_ADMIN


@pytest.mark.asyncio
async def test_subscribe_via_start_creates_destination_and_activates_subscription() -> None:
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    destination = _destination(tenant_id=tenant_id)
    subscription = _subscription(tenant_id=tenant_id, bot_id=bot_id, destination_id=destination.id)

    with (
        patch(
            "app.modules.destinations.service.get_or_create_destination",
            AsyncMock(return_value=destination),
        ) as get_or_create,
        patch(
            "app.modules.destinations.service.activate_subscription",
            AsyncMock(return_value=subscription),
        ) as activate,
    ):
        result_destination, result_subscription = await service.subscribe_via_start(
            AsyncMock(),
            tenant_id=tenant_id,
            bot_id=bot_id,
            type=DestinationType.PERSONAL,
            chat_id=123,
            thread_id=None,
            title="Someone",
        )

        assert result_destination is destination
        assert result_subscription is subscription
        get_or_create.assert_awaited_once()
        assert activate.await_args.kwargs == {
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "destination_id": destination.id,
        }


@pytest.mark.asyncio
async def test_unsubscribe_via_stop_returns_none_when_destination_missing() -> None:
    with patch("app.modules.destinations.service.DestinationRepository") as repo_cls:
        repo_cls.return_value.get_active_by_chat = AsyncMock(return_value=None)

        result = await service.unsubscribe_via_stop(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), chat_id=123, thread_id=None
        )

        assert result is None


@pytest.mark.asyncio
async def test_unsubscribe_via_stop_returns_none_when_subscription_missing() -> None:
    destination = _destination()
    with (
        patch("app.modules.destinations.service.DestinationRepository") as dest_repo_cls,
        patch("app.modules.destinations.service.SubscriptionRepository") as sub_repo_cls,
    ):
        dest_repo_cls.return_value.get_active_by_chat = AsyncMock(return_value=destination)
        sub_repo_cls.return_value.get_active = AsyncMock(return_value=None)

        result = await service.unsubscribe_via_stop(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), chat_id=123, thread_id=None
        )

        assert result is None


@pytest.mark.asyncio
async def test_is_actively_subscribed_returns_true_when_status_active() -> None:
    subscription = _subscription(status=SubscriptionStatus.ACTIVE)
    with patch("app.modules.destinations.service.SubscriptionRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=subscription)

        result = await service.is_actively_subscribed(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), destination_id=uuid.uuid4()
        )

        assert result is True


@pytest.mark.asyncio
async def test_is_actively_subscribed_returns_false_when_none_found() -> None:
    with patch("app.modules.destinations.service.SubscriptionRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=None)

        result = await service.is_actively_subscribed(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), destination_id=uuid.uuid4()
        )

        assert result is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [SubscriptionStatus.UNSUBSCRIBED, SubscriptionStatus.BLOCKED_BY_ADMIN]
)
async def test_is_actively_subscribed_returns_false_when_not_active(
    status: SubscriptionStatus,
) -> None:
    subscription = _subscription(status=status)
    with patch("app.modules.destinations.service.SubscriptionRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=subscription)

        result = await service.is_actively_subscribed(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), destination_id=uuid.uuid4()
        )

        assert result is False


@pytest.mark.asyncio
async def test_unsubscribe_via_stop_sets_status_unsubscribed() -> None:
    destination = _destination()
    subscription = _subscription(destination_id=destination.id, status=SubscriptionStatus.ACTIVE)
    with (
        patch("app.modules.destinations.service.DestinationRepository") as dest_repo_cls,
        patch("app.modules.destinations.service.SubscriptionRepository") as sub_repo_cls,
    ):
        dest_repo_cls.return_value.get_active_by_chat = AsyncMock(return_value=destination)
        sub_repo_cls.return_value.get_active = AsyncMock(return_value=subscription)

        result = await service.unsubscribe_via_stop(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), chat_id=123, thread_id=None
        )

        assert result is subscription
        assert result.status == SubscriptionStatus.UNSUBSCRIBED
