"""Base exception classes + global exception handler.

- `AppException`: custom base class for every business exception. Each
  module can define its own subclass (e.g. `BotTokenInvalidError`) with its
  own `code` + default `message`, mapped to the appropriate HTTP status.
- The global handler covers: `AppException`, `RequestValidationError`
  (Pydantic), FastAPI/Starlette's built-in `HTTPException`, and a generic
  `Exception` fallback (500 - full stack trace logged, response to the
  client stays generic).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.core.config import settings
from app.core.response import error_envelope
from app.i18n import translate as i18n_translate

logger = structlog.get_logger(__name__)


class AppException(Exception):
    """Base class for every business exception across all modules."""

    code: str = "APP_ERROR"
    message: str = "An error occurred."
    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str | None = None, *, details: list[Any] | None = None) -> None:
        self.message = message or self.message
        self.details = details or []
        super().__init__(self.message)


def _lang(request: Request) -> str:
    return getattr(request.state, "language", settings.i18n.default_language)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        message = i18n_translate(exc.code, _lang(request)) or exc.message
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(
                request=request, code=exc.code, message=message, details=exc.details
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        message = i18n_translate("VALIDATION_ERROR", _lang(request)) or "Invalid input."
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_envelope(
                request=request,
                code="VALIDATION_ERROR",
                message=message,
                # `jsonable_encoder`, not the raw `exc.errors()` - Pydantic v2
                # embeds the original exception object in `ctx.error` for
                # `ValueError`s raised manually from a custom
                # `@model_validator` (e.g. `MessageCreate` in
                # `modules/messaging/schema.py`), which isn't JSON-serializable
                # and would blow up `JSONResponse` into a 500 if sent as-is
                # (the default `json.dumps` doesn't know how to serialize an
                # exception instance). `jsonable_encoder` normalizes it
                # (stringifies `ctx.error`), same as FastAPI's default handler.
                details=jsonable_encoder(exc.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # `exc.detail` (e.g. "Not authenticated" from HTTPBearer, "Not Found"
        # from routing) is already specific to this exception - don't
        # overwrite it with the generic i18n "HTTP_ERROR" message; i18n is
        # only a fallback for when this exception has no detail at all.
        message = str(exc.detail) or (
            i18n_translate("HTTP_ERROR", _lang(request)) or "The request could not be processed."
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(request=request, code="HTTP_ERROR", message=message, details=[]),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log the full stack trace, but keep the response to the client
        # generic (never leak a stack trace to the caller).
        logger.exception("unhandled_exception", path=request.url.path)
        message = (
            i18n_translate("INTERNAL_SERVER_ERROR", _lang(request)) or "An internal error occurred."
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_envelope(
                request=request, code="INTERNAL_SERVER_ERROR", message=message, details=[]
            ),
        )
