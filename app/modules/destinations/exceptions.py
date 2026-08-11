"""Custom exceptions for the `destinations` module, subclassing `AppException`

(`app.core.exceptions`).

Scope: message destinations (chats/groups/channels) and their bot subscriptions.
"""

from __future__ import annotations

from fastapi import status

from app.core.exceptions import AppException


class DestinationNotFoundError(AppException):
    code = "DESTINATION_NOT_FOUND"
    message = "Destination not found."
    status_code = status.HTTP_404_NOT_FOUND


class DestinationAlreadyExistsError(AppException):
    code = "DESTINATION_ALREADY_EXISTS"
    message = "A destination with this chat/thread already exists in this organization."
    status_code = status.HTTP_409_CONFLICT


class SubscriptionNotFoundError(AppException):
    code = "SUBSCRIPTION_NOT_FOUND"
    message = "This destination is not subscribed to that bot."
    status_code = status.HTTP_404_NOT_FOUND
