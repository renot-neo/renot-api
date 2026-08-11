"""Pydantic schemas for the `destinations` module.

Scope: message destinations (chats/groups/channels) and their bot subscriptions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.destinations.model import DestinationType, SubscriptionStatus


class DestinationCreate(BaseModel):
    # Manual registration from the dashboard (mainly for `channel` - a bot
    # is added as channel admin manually in Telegram, then the
    # `channel_id` is registered here) - immediately linked to one bot via
    # `bot_id`, with its subscription created as `active` (see service.py).
    bot_id: uuid.UUID = Field(description="The bot to subscribe to this destination.")
    type: DestinationType
    chat_id: int = Field(description="The Telegram chat ID (personal/group/channel).")
    thread_id: int | None = Field(
        default=None, description="Thread/topic ID - only set for the `group_thread` type."
    )
    title: str = Field(min_length=1, max_length=255)


class DestinationUpdate(BaseModel):
    # `chat_id`/`thread_id`/`type` identify the actual Telegram entity - they
    # cannot be changed via PATCH, only the local label (`title`) is
    # mutable, same pattern as `BotUpdate.name`.
    title: str | None = Field(default=None, min_length=1, max_length=255)


class SubscriptionUpdate(BaseModel):
    # The endpoint path (`PATCH /destinations/{id}/subscription`) doesn't
    # carry a bot ID - a single destination can be subscribed to more than
    # one bot, so `bot_id` must be given in the body to identify which
    # subscription is being changed.
    bot_id: uuid.UUID
    status: SubscriptionStatus = Field(
        description="The subscription's new status - used by admin/owner to "
        "manually unsubscribe (`unsubscribed`) or block (`blocked_by_admin`) "
        "from the dashboard."
    )


class DestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    type: DestinationType
    chat_id: int
    thread_id: int | None
    title: str
    created_at: datetime


class DestinationSubscriptionResponse(DestinationResponse):
    """`DestinationResponse` + its subscription status to one specific bot -

    used by `GET /bots/{bot_id}/destinations`.
    """

    subscription_status: SubscriptionStatus


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bot_id: uuid.UUID
    destination_id: uuid.UUID
    status: SubscriptionStatus
    created_at: datetime
