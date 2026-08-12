"""Unit tests for `app.i18n.translate` - the English-fallback branch

specifically (the "found directly" path is already exercised indirectly
by every error-response test across the suite, e.g.
`tests/unit/test_core_exceptions.py`). Pure logic, no DB/network needed.
"""

from __future__ import annotations

from app import i18n


def test_translate_falls_back_to_english_when_key_missing_in_target_locale(
    monkeypatch,
) -> None:
    """`en.json`/`id.json` currently share the exact same key set (nothing to

    fall back FROM today), so this monkeypatches `_load_locale` directly
    rather than relying on the two files staying coincidentally divergent -
    the fallback needs to keep working the day a key IS added to one locale
    before the other.
    """
    monkeypatch.setattr(
        i18n,
        "_load_locale",
        lambda lang: {"ONLY_IN_ENGLISH": "english message"} if lang == "en" else {},
    )

    assert i18n.translate("ONLY_IN_ENGLISH", lang="id") == "english message"


def test_translate_returns_none_when_key_missing_from_every_locale(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "_load_locale", lambda lang: {})

    assert i18n.translate("NOWHERE_AT_ALL", lang="id") is None
