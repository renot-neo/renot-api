"""Redis-based rate limiting middleware.

Fixed-window Redis counter (INCR+EXPIRE on the Nth window) - the same
pattern as the per-bot Telegram throttle in
`modules/messaging/tasks.py::_try_consume_window`, just generalized to a
custom window (not only 1 second). Chosen over a sliding-window-log or
token-bucket+Lua approach because burst-at-window-boundary precision is
good enough for this kind of API protection.

3 distinct limit groups, all using a 60-second window:
- `auth` (limit 10/60s): `/api/v1/auth/*` endpoints (register/login/refresh
  etc.) - strict, to prevent brute force. Keyed by client IP - there's no
  user/tenant identity yet before login succeeds.
- `messaging_send` (limit 60/60s): `POST /api/v1/messages` - intended to
  eventually follow the billing plan's quota. The MVP only has a single
  unlimited/unpriced "Default" `Plan`, so this limit is hardcoded flat for
  now rather than read from the `Plan` row - TODO: make it a per-`Plan`
  field once real pricing tiers are designed. Keyed by `tenant_id` (from the
  active JWT claim) - matching how billing quota is per-organization, not
  per individual user; this endpoint also accepts `X-Bot-Api-Key` auth (see
  `modules/messaging/deps.py`) - when present, keyed by a hash of the key
  itself (a stable per-bot identity without a DB lookup in this middleware,
  which is deliberately kept best-effort/cheap like the JWT path); falls
  back to IP when neither is present/valid (such a request will get a 401
  from the route dependency anyway, but it's still counted so that spam
  attempts without any credentials are also rate-limited).
- `general` (limit 300/60s): every other endpoint - lenient. Keyed by
  `user_id` (JWT `sub` claim) when available, falling back to IP.

Response when the limit is exceeded: 429 + a `Retry-After` header (seconds
until the next window) + an error envelope body (`core/response.error_envelope`)
- built DIRECTLY here, NOT via `raise AppException`. This custom middleware
(`app.add_middleware`) sits OUTSIDE Starlette's built-in `ExceptionMiddleware`
on the ASGI stack - an exception raised before `call_next()` is called would
never reach the handlers in `core/exceptions.py`, only get caught by
`ServerErrorMiddleware` as a generic 500. The `RATE_LIMIT_EXCEEDED` error
code is still registered in `app/i18n/{en,id}.json` for consistent message
formatting, even though the language lookup here reads the
`Accept-Language` header directly (NOT `request.state.language`) - this
middleware runs BEFORE `I18nMiddleware` in `app/main.py`, so that state
isn't set yet when a request gets blocked here.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import redis.asyncio as redis
import structlog
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.deps import get_redis
from app.core.response import error_envelope
from app.core.security import decode_token
from app.i18n import translate as i18n_translate

logger = structlog.get_logger(__name__)

_AUTH_PATH_PREFIX = "/api/v1/auth"
_MESSAGES_PATH = "/api/v1/messages"

_WINDOW_SECONDS = 60
_AUTH_LIMIT = 10
_MESSAGING_SEND_LIMIT = 60
_GENERAL_LIMIT = 300


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _decode_jwt_payload(request: Request) -> dict[str, Any] | None:
    """Best-effort decode, never raises - this middleware doesn't validate

    auth (that's `core/deps.get_current_user`/`get_current_tenant`'s job at
    the route dependency level); it only needs the `sub`/`tenant_id` claims
    for the rate-limit key when they're available.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[len("bearer ") :].strip()
    try:
        return decode_token(token)
    except JWTError:
        return None


def _resolve_language(request: Request) -> str:
    from app.core.config import settings

    header_value = request.headers.get("accept-language", "")
    lang = header_value.split(",")[0].split("-")[0].strip().lower()
    return lang or settings.i18n.default_language


def classify_request(request: Request) -> tuple[str, int, str]:
    """Determine (group, limit, redis_key) for this request. Split out from

    `RateLimitMiddleware.dispatch` so it can be tested directly without
    building a full FastAPI app.
    """
    path = request.url.path

    if path.startswith(_AUTH_PATH_PREFIX):
        return "auth", _AUTH_LIMIT, f"ratelimit:auth:{_client_ip(request)}"

    if path == _MESSAGES_PATH and request.method == "POST":
        payload = _decode_jwt_payload(request)
        tenant_id = payload.get("tenant_id") if payload else None
        if tenant_id:
            identifier = str(tenant_id)
        elif api_key := request.headers.get("x-bot-api-key"):
            # This endpoint also accepts `X-Bot-Api-Key` auth - just hash it
            # (NOT a DB lookup, this middleware stays best-effort/cheap) so
            # the raw key never ends up in a Redis key or a log, while still
            # giving a stable per-bot identifier (unlike the IP fallback
            # below, which can be shared by many bots).
            identifier = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        else:
            identifier = _client_ip(request)
        return "messaging_send", _MESSAGING_SEND_LIMIT, f"ratelimit:messaging_send:{identifier}"

    payload = _decode_jwt_payload(request)
    user_id = payload.get("sub") if payload else None
    identifier = str(user_id) if user_id else _client_ip(request)
    return "general", _GENERAL_LIMIT, f"ratelimit:general:{identifier}"


async def try_consume_window(
    redis_client: redis.Redis, key: str, limit: int, window_seconds: int = _WINDOW_SECONDS
) -> tuple[bool, int]:
    """INCR+EXPIRE on window `int(time.time()) // window_seconds`.

    Returns `(allowed, retry_after_seconds)` - `retry_after_seconds` is
    computed even when `allowed` is True so callers can reuse it without an
    extra branch.
    """
    now = int(time.time())
    window = now // window_seconds
    redis_key = f"{key}:{window}"
    count = await redis_client.incr(redis_key)
    if count == 1:
        await redis_client.expire(redis_key, window_seconds)
    retry_after = window_seconds - (now % window_seconds)
    return count <= limit, retry_after


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        group, limit, key = classify_request(request)

        redis_client = await get_redis()
        allowed, retry_after = await try_consume_window(redis_client, key, limit)
        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                group=group,
                path=request.url.path,
                method=request.method,
                key=key,
                limit=limit,
                retry_after=retry_after,
            )
            lang = _resolve_language(request)
            message = i18n_translate("RATE_LIMIT_EXCEEDED", lang) or "Too many requests."
            response = JSONResponse(
                status_code=429,
                content=error_envelope(
                    request=request, code="RATE_LIMIT_EXCEEDED", message=message, details=[]
                ),
            )
            response.headers["Retry-After"] = str(retry_after)
            return response

        return await call_next(request)
