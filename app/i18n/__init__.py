"""Locale loader for error messages - keys map to `error.code`.

Locale files are stored at `app/i18n/<lang>.json`. Default fallback: `en`
(see `settings.i18n.default_language`). Only system messages are
translatable - there's no translatable content at the data level.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_LOCALE_DIR = Path(__file__).parent


@cache
def _load_locale(lang: str) -> dict[str, str]:
    path = _LOCALE_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def translate(code: str, lang: str = "en") -> str | None:
    """Looks up the message for `code` in locale `lang`, falling back to `en` if missing."""
    message = _load_locale(lang).get(code)
    if message is None and lang != "en":
        message = _load_locale("en").get(code)
    return message
