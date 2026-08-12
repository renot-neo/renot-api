"""Business logic for the `destinations` module.

Scope: message destinations (chats/groups/channels) and their bot subscriptions.

This is the layer the router calls into - the only place business logic
belongs.

Bots are validated via `app.modules.bots.get_bot` (the public service
interface) - this module MUST NOT `from app.modules.bots.model import Bot`
directly.

`get_or_create_destination`/`activate_subscription`/`subscribe_via_start`/
`unsubscribe_via_stop` below are auto-subscribe helpers used by
`modules/webhooks` when processing `/start`/`/stop` - DIFFERENT from
`create_destination`/`update_subscription_status` above, which are the
manual dashboard path. The two are deliberately kept separate: the manual
path is always an explicit request from an authorized user (owner/admin)
via HTTP+RBAC, while the `/start`/`/stop` path is triggered by Telegram
itself (no permission check - anyone chatting with the bot can `/start`)
and must never reject "chat already registered" the way `create_destination`
does (`DestinationAlreadyExistsError`) - a repeated `/start` must be
idempotent, not an error.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.bots import get_bot
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
from app.modules.destinations.repository import DestinationRepository, SubscriptionRepository


async def create_destination(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    type: DestinationType,
    chat_id: int,
    thread_id: int | None,
    title: str,
) -> Destination:
    """Manual destination registration from the dashboard - immediately

    creates an `active` subscription to the given `bot_id`. Raises
    `BotNotFoundError` (from `modules/bots`) if the bot isn't found in this
    tenant.
    """
    await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)

    destinations = DestinationRepository(session)
    if (
        await destinations.get_active_by_chat(
            tenant_id=tenant_id, chat_id=chat_id, thread_id=thread_id
        )
        is not None
    ):
        raise DestinationAlreadyExistsError()

    destination = await destinations.create(
        tenant_id=tenant_id, type=type, chat_id=chat_id, thread_id=thread_id, title=title
    )
    await SubscriptionRepository(session).create(
        tenant_id=tenant_id, bot_id=bot_id, destination_id=destination.id
    )
    return destination


async def get_destination(
    session: AsyncSession, *, tenant_id: uuid.UUID, destination_id: uuid.UUID
) -> Destination:
    destination = await DestinationRepository(session).get_active(
        tenant_id=tenant_id, destination_id=destination_id
    )
    if destination is None:
        raise DestinationNotFoundError()
    return destination


async def list_destinations(
    session: AsyncSession, *, tenant_id: uuid.UUID, page_params: PageParams
) -> Page[Destination]:
    return await DestinationRepository(session).list_active(tenant_id=tenant_id, params=page_params)


async def list_destinations_for_bot(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, page_params: PageParams
) -> Page[tuple[Destination, BotDestinationSubscription]]:
    """Used by `GET /bots/{bot_id}/destinations` - lists the destinations

    subscribed to this bot along with their subscription status. Pagination
    is driven from `BotDestinationSubscription` (the pivot table) - the
    `Destination` lookup per item is still done per-row (not a JOIN,
    consistent with this module's style - see the `billing/repository.py`
    docstring), but it's now bounded to a single page's worth, not an
    unbounded N+1.
    """
    await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)

    subscriptions_page = await SubscriptionRepository(session).list_active_for_bot(
        tenant_id=tenant_id, bot_id=bot_id, params=page_params
    )
    items: list[tuple[Destination, BotDestinationSubscription]] = []
    for subscription in subscriptions_page.items:
        destination = await DestinationRepository(session).get_active(
            tenant_id=tenant_id, destination_id=subscription.destination_id
        )
        if destination is not None:
            items.append((destination, subscription))
    return Page(
        items=items,
        total=subscriptions_page.total,
        page=subscriptions_page.page,
        page_size=subscriptions_page.page_size,
    )


async def update_destination(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    destination_id: uuid.UUID,
    title: str | None = None,
) -> Destination:
    destination = await get_destination(session, tenant_id=tenant_id, destination_id=destination_id)
    if title is not None:
        destination.title = title
    await session.flush()
    return destination


async def delete_destination(
    session: AsyncSession, *, tenant_id: uuid.UUID, destination_id: uuid.UUID
) -> None:
    """Soft-deletes the destination + cascade soft-deletes all of its active

    subscriptions.
    """
    destination = await get_destination(session, tenant_id=tenant_id, destination_id=destination_id)

    subscriptions = SubscriptionRepository(session)
    for subscription in await subscriptions.list_active_for_destination(
        tenant_id=tenant_id, destination_id=destination_id
    ):
        await subscriptions.soft_delete(subscription)

    await DestinationRepository(session).soft_delete(destination)


async def update_subscription_status(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    destination_id: uuid.UUID,
    bot_id: uuid.UUID,
    status: SubscriptionStatus,
) -> BotDestinationSubscription:
    """Manual unsubscribe / block by an admin/owner from the dashboard.

    Raises `DestinationNotFoundError`/`BotNotFoundError` if either doesn't
    exist in this tenant, `SubscriptionNotFoundError` if the (bot,
    destination) pair has never subscribed.
    """
    await get_destination(session, tenant_id=tenant_id, destination_id=destination_id)
    await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)

    subscriptions = SubscriptionRepository(session)
    subscription = await subscriptions.get_active(
        tenant_id=tenant_id, bot_id=bot_id, destination_id=destination_id
    )
    if subscription is None:
        raise SubscriptionNotFoundError()

    subscription.status = status
    await session.flush()
    return subscription


async def get_or_create_destination(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    type: DestinationType,
    chat_id: int,
    thread_id: int | None,
    title: str,
) -> Destination:
    """Get-or-create by `(tenant_id, chat_id, thread_id)` - used by the

    auto-subscribe flow when a `/start` comes in from a chat that may not
    yet be registered as a `Destination`. If the destination already
    exists, its stored `title` is NOT overwritten by a new `title` from a
    Telegram update - the title is metadata managed manually via
    `update_destination`, same as the dashboard path.
    """
    existing = await DestinationRepository(session).get_active_by_chat(
        tenant_id=tenant_id, chat_id=chat_id, thread_id=thread_id
    )
    if existing is not None:
        return existing
    return await DestinationRepository(session).create(
        tenant_id=tenant_id, type=type, chat_id=chat_id, thread_id=thread_id, title=title
    )


async def activate_subscription(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, destination_id: uuid.UUID
) -> BotDestinationSubscription:
    """Creates an `active` subscription if none exists, or sets it back to

    `active` if it was previously `unsubscribed` (re-subscribing via
    `/start` after a `/stop`). A `blocked_by_admin` subscription is
    DELIBERATELY NOT auto-reactivated - blocking is an admin/owner decision
    made through the dashboard (`update_subscription_status`), not something
    a user should be able to override themselves via `/start`. Blocked
    stays blocked; unblocking is only possible through the dashboard.
    """
    subscriptions = SubscriptionRepository(session)
    existing = await subscriptions.get_active(
        tenant_id=tenant_id, bot_id=bot_id, destination_id=destination_id
    )
    if existing is None:
        return await subscriptions.create(
            tenant_id=tenant_id, bot_id=bot_id, destination_id=destination_id
        )
    if existing.status == SubscriptionStatus.UNSUBSCRIBED:
        existing.status = SubscriptionStatus.ACTIVE
        await session.flush()
    return existing


async def subscribe_via_start(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    type: DestinationType,
    chat_id: int,
    thread_id: int | None,
    title: str,
) -> tuple[Destination, BotDestinationSubscription]:
    """Full auto-subscribe flow: get-or-create the destination, then

    activate its subscription. Called by `modules/webhooks` ONLY after the
    caller has already checked `Bot.webhook_enabled` is `true` - if the
    policy is closed, there must be no new `Destination`/`Subscription` at
    all (not "created but not activated"), so that check is deliberately
    not repeated here.
    """
    destination = await get_or_create_destination(
        session, tenant_id=tenant_id, type=type, chat_id=chat_id, thread_id=thread_id, title=title
    )
    subscription = await activate_subscription(
        session, tenant_id=tenant_id, bot_id=bot_id, destination_id=destination.id
    )
    return destination, subscription


async def is_actively_subscribed(
    session: AsyncSession, *, tenant_id: uuid.UUID, bot_id: uuid.UUID, destination_id: uuid.UUID
) -> bool:
    """Used by `modules/messaging` to validate that a requested destination

    is actually **actively** subscribed to this bot before a `Message` is
    created. Different from `SubscriptionRepository.get_active` (that name
    only means "not soft-deleted", NOT "status == ACTIVE") -
    `unsubscribed`/`blocked_by_admin` subscriptions still pass `get_active`
    but must be treated as NOT sendable here.
    """
    subscription = await SubscriptionRepository(session).get_active(
        tenant_id=tenant_id, bot_id=bot_id, destination_id=destination_id
    )
    return subscription is not None and subscription.status == SubscriptionStatus.ACTIVE


async def get_subscription_status(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    chat_id: int,
    thread_id: int | None,
) -> SubscriptionStatus | None:
    """Read-only lookup for `/status` (used by `modules/webhooks`) - `None`

    means this chat has never `/start`ed this bot (no `Destination` exists
    yet for it), as distinct from an existing-but-inactive subscription
    (`unsubscribed`/`blocked_by_admin`), which returns that actual status.
    """
    destination = await DestinationRepository(session).get_active_by_chat(
        tenant_id=tenant_id, chat_id=chat_id, thread_id=thread_id
    )
    if destination is None:
        return None
    subscription = await SubscriptionRepository(session).get_active(
        tenant_id=tenant_id, bot_id=bot_id, destination_id=destination.id
    )
    return subscription.status if subscription is not None else None


async def unsubscribe_via_stop(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    chat_id: int,
    thread_id: int | None,
) -> BotDestinationSubscription | None:
    """`/stop` - self-service unsubscribe. Returns `None` when no matching

    destination/subscription is found (this chat never `/start`ed) so
    `modules/webhooks` can reply with a different message for that case,
    instead of raising `DestinationNotFoundError`/`SubscriptionNotFoundError`
    like the manual dashboard path - `/stop` from a chat that was never
    subscribed isn't an error condition, just a no-op.
    """
    destination = await DestinationRepository(session).get_active_by_chat(
        tenant_id=tenant_id, chat_id=chat_id, thread_id=thread_id
    )
    if destination is None:
        return None

    subscriptions = SubscriptionRepository(session)
    subscription = await subscriptions.get_active(
        tenant_id=tenant_id, bot_id=bot_id, destination_id=destination.id
    )
    if subscription is None:
        return None

    subscription.status = SubscriptionStatus.UNSUBSCRIBED
    await session.flush()
    return subscription
