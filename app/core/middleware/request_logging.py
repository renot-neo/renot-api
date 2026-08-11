"""Request logging middleware.

Logs, per request: `request_id`, method, path, status_code, duration_ms, IP
address, user-agent, `tenant_id` (if any), `user_id` (if any). `request_id`
is a generated UUID, propagated via `request.state` + structlog contextvars,
and attached to every log emitted during that request's lifecycle.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # `call_next` already goes through FastAPI's exception handlers
        # (core/exceptions.py), so `response` here is always set even when the
        # request ends in an error/AppException.
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            tenant_id=getattr(request.state, "tenant_id", None),
            user_id=getattr(request.state, "user_id", None),
        )
        structlog.contextvars.clear_contextvars()

        response.headers["X-Request-ID"] = request_id
        return response
