"""Custom exceptions for the `auth` module, subclassing `AppException`

(`app.core.exceptions`).

Scope: authentication (register, login, refresh, logout) and user/role identity.
"""

from __future__ import annotations

from fastapi import status

from app.core.exceptions import AppException


class EmailAlreadyRegisteredError(AppException):
    code = "EMAIL_ALREADY_REGISTERED"
    message = "This email is already registered."
    status_code = status.HTTP_409_CONFLICT


class InvalidCredentialsError(AppException):
    code = "INVALID_CREDENTIALS"
    message = "Email or password is incorrect."
    status_code = status.HTTP_401_UNAUTHORIZED


class UserInactiveError(AppException):
    code = "USER_INACTIVE"
    message = "This account has been deactivated."
    status_code = status.HTTP_403_FORBIDDEN


class TokenInvalidError(AppException):
    code = "TOKEN_INVALID"
    message = "The token is invalid or has expired."
    status_code = status.HTTP_401_UNAUTHORIZED


class RefreshTokenInvalidError(AppException):
    code = "REFRESH_TOKEN_INVALID"
    message = "The refresh token is invalid, expired, or has been revoked."
    status_code = status.HTTP_401_UNAUTHORIZED


class NoActiveOrganizationError(AppException):
    code = "NO_ACTIVE_ORGANIZATION"
    message = "No active organization in this session - call switch-organization first."
    status_code = status.HTTP_409_CONFLICT
