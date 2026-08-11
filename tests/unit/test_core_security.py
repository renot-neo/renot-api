"""Unit tests for `app.core.security.encrypt_secret`/`decrypt_secret` -

the Fernet-based helpers backing `Bot.token`/`Bot.webhook_secret` at-rest
encryption (see `private/specs/2026-08-12-bot-secret-encryption-design.md`).
Pure functions, no DB/network needed.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from app.core.security import decrypt_secret, encrypt_secret


def test_encrypt_then_decrypt_round_trips_to_original_plaintext() -> None:
    plaintext = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

    ciphertext = encrypt_secret(plaintext)
    decrypted = decrypt_secret(ciphertext)

    assert decrypted == plaintext


def test_encrypt_secret_output_is_not_the_plaintext() -> None:
    plaintext = "tgbm_live_super-secret-value"

    ciphertext = encrypt_secret(plaintext)

    assert ciphertext != plaintext
    assert plaintext not in ciphertext


def test_encrypt_secret_is_not_deterministic() -> None:
    """Fernet includes a random IV/nonce and timestamp per call - encrypting

    the same plaintext twice must not produce the same ciphertext (defends
    against a DB-dump comparison revealing which bots share a token).
    """
    plaintext = "same-value"

    assert encrypt_secret(plaintext) != encrypt_secret(plaintext)


def test_decrypt_secret_raises_on_tampered_ciphertext() -> None:
    ciphertext = encrypt_secret("original-value")
    tampered = ciphertext[:-4] + ("A" if ciphertext[-4] != "A" else "B") + ciphertext[-3:]

    with pytest.raises(InvalidToken):
        decrypt_secret(tampered)


def test_decrypt_secret_raises_on_garbage_input() -> None:
    with pytest.raises(InvalidToken):
        decrypt_secret("not-a-real-fernet-token")
