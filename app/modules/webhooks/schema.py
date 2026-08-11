"""Pydantic schemas for the `webhooks` module.

Scope: processing inbound Telegram webhooks.

This endpoint's request body (`POST /webhooks/telegram/{bot_id}`) uses
`TelegramUpdate` from `app.shared.telegram_types` directly - not a local
`<Entity>Create` schema like other modules, because the payload ISN'T a
structure we get to design (it must match Telegram's `Update` object schema
exactly) and is already centralized in `shared/telegram_types.py` (a custom
Pydantic type shared across the `messaging`, `bots`, and `webhooks`
modules). The response also needs no entity schema - this endpoint just
replies with a generic ok status via the regular envelope
(`success_envelope({"status": "ok"})`); there's no domain data to serialize
to the client (the client here is Telegram, which doesn't read the response body).
"""

from __future__ import annotations
