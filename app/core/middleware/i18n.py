"""i18n middleware.

Determines `request.state.language` from the `Accept-Language` header,
falling back to `settings.i18n.default_language` (`en`). Used by
`core/exceptions.py` to pick the error message's language.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings


class I18nMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        header_value = request.headers.get("accept-language", "")
        # Only take the first language (e.g. "id-ID,id;q=0.9,en;q=0.8" -> "id"),
        # full q-value negotiation isn't needed yet.
        lang = header_value.split(",")[0].split("-")[0].strip().lower()
        request.state.language = lang or settings.i18n.default_language
        return await call_next(request)
