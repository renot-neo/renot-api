"""JWT encode/decode, password hashing, symmetric secret encryption.

Pure utilities - no DB access, no knowledge of the `User`/`Bot` models.
Callers (e.g. `modules/auth/service.py`, `modules/bots/service.py`) are
responsible for loading/persisting data and translating errors raised here
into the appropriate `AppException`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
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


def encrypt_secret(plaintext: str) -> str:
    """Fernet-encrypt a high-entropy secret for at-rest storage (e.g.

    `Bot.token`/`Bot.webhook_secret` - see
    `private/specs/2026-08-12-bot-secret-encryption-design.md`). Not for
    passwords - `hash_password` above is the one-way, slow-hash tool for
    those; this is reversible symmetric encryption for secrets the app
    itself needs to read back later (a Telegram bot token, an HMAC key).

    A fresh random IV/IV+timestamp goes into every call (Fernet's own
    design), so encrypting the same plaintext twice yields different
    ciphertext - never compare ciphertext for equality, decrypt and compare
    plaintext instead.
    """
    fernet = Fernet(settings.telegram.token_encryption_key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Inverse of `encrypt_secret`. Raises `cryptography.fernet.InvalidToken`

    on tampered/corrupted ciphertext or a wrong key - Fernet's own
    authentication (HMAC), not something this wrapper adds.
    """
    fernet = Fernet(settings.telegram.token_encryption_key)
    return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


__all__ = [
    "JWTError",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "encrypt_secret",
    "decrypt_secret",
]
