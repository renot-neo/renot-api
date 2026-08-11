"""Pydantic schemas for the `bots` module.

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255, description="The bot's label in the dashboard.")
    token: str = Field(min_length=1, description="Token from @BotFather, e.g. `123456:ABC-DEF...`.")
    outbound_callback_url: str | None = Field(default=None, max_length=2048)


class BotUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    outbound_callback_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Omitting the field or sending `null` leaves it unchanged. "
        'Send `""` (empty string) to clear an existing callback URL.',
    )


class BotSubscriptionPolicyUpdate(BaseModel):
    webhook_enabled: bool = Field(
        description="Open (`true`) or close (`false`) subscription via `/start`."
    )


class BotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    telegram_bot_id: int
    username: str
    token_last_four: str
    webhook_enabled: bool
    api_key_prefix: str
    outbound_callback_url: str | None
    created_at: datetime


class BotSecretResponse(BotResponse):
    """`BotResponse` + the plaintext API key - used ONLY in the response for

    registering a new bot and for regenerate-key (the API key is shown only
    once, at generate/regenerate time). Every other endpoint (get/list)
    always uses the plain `BotResponse`, and never returns the plaintext key.
    """

    api_key: str


class BotAssignmentCreate(BaseModel):
    user_id: uuid.UUID = Field(
        description="The user to assign - must already be a member of the active organization."
    )


class BotAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    bot_id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
