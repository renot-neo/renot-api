"""JWT encode/decode, password hashing.

Pure utilities - no DB access, no knowledge of the `User` model. Callers
(e.g. `modules/auth/service.py`) are responsible for loading/persisting data
and translating errors raised here into the appropriate `AppException`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# argon2id: the recommended password hashing scheme when available.
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    *, subject: str, tenant_id: str | None, expires_delta: timedelta | None = None
) -> str:
    """Short-lived access token. Minimal payload: `sub`, `tenant_id`, `jti`."""
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.jwt.access_token_expire_minutes)
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "jti": str(uuid.uuid4()),
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt.secret_key, algorithm=settings.jwt.algorithm)


def create_refresh_token(*, subject: str, expires_delta: timedelta | None = None) -> str:
    """Long-lived refresh token - stored hashed in the DB so it can be revoked.

    Hashing/persistence of the token happens in `modules/auth/service.py`, not here.
    """
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(days=settings.jwt.refresh_token_expire_days)
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": str(uuid.uuid4()),
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt.secret_key, algorithm=settings.jwt.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode & verify a JWT. Raises `jose.JWTError` if invalid/expired -

    the caller (`core/deps.py`) translates this into the appropriate
    `AppException` (e.g. `TokenInvalidError`).
    """
    return jwt.decode(token, settings.jwt.secret_key, algorithms=[settings.jwt.algorithm])


__all__ = [
    "JWTError",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
]
