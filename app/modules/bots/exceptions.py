"""Custom exceptions for the `bots` module, subclassing `AppException`

(`app.core.exceptions`).

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.
"""

from __future__ import annotations

from fastapi import status

from app.core.exceptions import AppException


class BotNotFoundError(AppException):
    code = "BOT_NOT_FOUND"
    message = "Bot not found."
    status_code = status.HTTP_404_NOT_FOUND


class BotTokenInvalidError(AppException):
    code = "BOT_TOKEN_INVALID"
    message = "The bot token is invalid or has been revoked."
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class BotAlreadyRegisteredError(AppException):
    code = "BOT_ALREADY_REGISTERED"
    message = "This Telegram bot is already registered on the platform."
    status_code = status.HTTP_409_CONFLICT


class BotWebhookSetupFailedError(AppException):
    code = "BOT_WEBHOOK_SETUP_FAILED"
    message = "Failed to configure the bot webhook with Telegram. Please try again."
    status_code = status.HTTP_502_BAD_GATEWAY


class BotAssignmentNotFoundError(AppException):
    code = "BOT_ASSIGNMENT_NOT_FOUND"
    message = "This user is not assigned to this bot."
    status_code = status.HTTP_404_NOT_FOUND


class BotAssignmentAlreadyExistsError(AppException):
    code = "BOT_ASSIGNMENT_ALREADY_EXISTS"
    message = "This user is already assigned to this bot."
    status_code = status.HTTP_409_CONFLICT


class BotAssignmentUserNotMemberError(AppException):
    code = "BOT_ASSIGNMENT_USER_NOT_MEMBER"
    message = "This user is not a member of the bot's organization."
    status_code = status.HTTP_404_NOT_FOUND


class BotApiKeyInvalidError(AppException):
    """The `X-Bot-Api-Key` header (dual-auth for the external message-send

    endpoint) is present but doesn't match any `Bot.api_key_hash` (wrong,
    already regenerated, or the bot has been soft-deleted) - 401, not 404,
    because this is purely a credential problem (same semantics as
    `TokenInvalidError` in `modules/auth`), not a missing resource.
    """

    code = "BOT_API_KEY_INVALID"
    message = "Invalid or revoked bot API key."
    status_code = status.HTTP_401_UNAUTHORIZED
