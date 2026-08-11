"""Custom exceptions for the `webhooks` module, subclassing `AppException`

(`app.core.exceptions`).

Scope: processing inbound Telegram webhooks.

`BotNotFoundError` (the `bot_id` in the path wasn't found/is soft-deleted)
is reused as-is from `app.modules.bots.exceptions` via
`get_bot_for_webhook` - not duplicated here.
"""

from __future__ import annotations

from fastapi import status

from app.core.exceptions import AppException


class WebhookSecretInvalidError(AppException):
    # Every inbound Telegram webhook must be validated via
    # X-Telegram-Bot-Api-Secret-Token.
    code = "WEBHOOK_SECRET_INVALID"
    message = "Webhook secret token is missing or invalid."
    status_code = status.HTTP_401_UNAUTHORIZED
