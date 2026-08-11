"""Custom exceptions for the `messaging` module, subclassing `AppException`

(`app.core.exceptions`).

Scope: sending messages, templates, scheduling, and delivery tracking.
"""

from __future__ import annotations

from fastapi import status

from app.core.exceptions import AppException


class MessageNotFoundError(AppException):
    code = "MESSAGE_NOT_FOUND"
    message = "Message not found."
    status_code = status.HTTP_404_NOT_FOUND


class MessageTemplateNotFoundError(AppException):
    code = "MESSAGE_TEMPLATE_NOT_FOUND"
    message = "Message template not found."
    status_code = status.HTTP_404_NOT_FOUND


class DestinationNotSubscribedError(AppException):
    code = "DESTINATION_NOT_SUBSCRIBED"
    message = "One or more destinations are not actively subscribed to this bot."
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class ScheduledAtInPastError(AppException):
    code = "SCHEDULED_AT_IN_PAST"
    message = "scheduled_at must be in the future."
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class TemplateVariableMissingError(AppException):
    code = "TEMPLATE_VARIABLE_MISSING"
    message = "One or more template variables required by the template body are missing."
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class BotNotAssignedError(AppException):
    """The MEMBER role tried `message:send`/`log:view` on a bot that isn't

    assigned to them - lives here (not `bots/exceptions.py`) because it's
    the calling module that raises it, same pattern as
    `DestinationNotSubscribedError` above (checked via
    `destinations.is_actively_subscribed`, but the exception "belongs" to
    `messaging`).
    """

    code = "BOT_NOT_ASSIGNED"
    message = "You are not assigned to this bot."
    status_code = status.HTTP_403_FORBIDDEN
