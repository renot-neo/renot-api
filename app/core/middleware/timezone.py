"""Timezone middleware.

All time data is stored in the DB as UTC. This middleware only determines
`request.state.timezone` from the `X-Timezone` header (falling back to
`settings.timezone.default_timezone` - default `Asia/Jakarta`), used by the
response serializer at the schema/service layer when converting UTC
timestamps to the user's timezone.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings


class TimezoneMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.timezone = request.headers.get(
            "x-timezone", settings.timezone.default_timezone
        )
        return await call_next(request)
