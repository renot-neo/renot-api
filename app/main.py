"""FastAPI entrypoint - registers routers & middleware.

URL versioning: every domain route is prefixed `/api/v1/...`. Router
modules are registered here once their concrete endpoints are available.
"""

from __future__ import annotations

import logging
import sys

import structlog
from fastapi import FastAPI

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.middleware.i18n import I18nMiddleware
from app.core.middleware.rate_limit import RateLimitMiddleware
from app.core.middleware.request_logging import RequestLoggingMiddleware
from app.core.middleware.timezone import TimezoneMiddleware
from app.core.response import success_envelope
from app.modules.auth.router import router as auth_router
from app.modules.billing.router import router as billing_router
from app.modules.bots.router import router as bots_router
from app.modules.destinations.router import bot_destinations_router
from app.modules.destinations.router import router as destinations_router
from app.modules.messaging.router import router as messaging_router
from app.modules.messaging.router import templates_router as message_templates_router
from app.modules.organizations.router import router as organizations_router
from app.modules.webhooks.router import router as webhooks_router

API_V1_PREFIX = "/api/v1"


def configure_logging() -> None:
    """Structured logging (structlog) to stdout, JSON format."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.environment == "development" else logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def create_app() -> FastAPI:
    configure_logging()

    # Interactive docs UI is disabled in production to reduce reconnaissance
    # surface (the full endpoint/schema map isn't handed to anyone who visits
    # a production URL). `openapi_url` stays enabled in every environment -
    # only the browsable /docs and /redoc UIs are gated.
    docs_enabled = settings.environment != "production"
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
    )

    register_exception_handlers(app)

    # Middleware order: request logging is outermost (wraps everything) so
    # duration_ms & status_code cover the other middleware; the i18n/timezone
    # context needs to be set before the request_logging handler reads its
    # state. (Tenant context is NOT set via middleware - that's
    # `core/deps.get_current_tenant`'s job, a dependency that runs per-route
    # after decoding the JWT; see its docstring. The
    # `request.state.tenant_id` read by `RequestLoggingMiddleware` below is
    # already populated from there.)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(I18nMiddleware)
    app.add_middleware(TimezoneMiddleware)

    app.include_router(auth_router, prefix=API_V1_PREFIX)
    app.include_router(organizations_router, prefix=API_V1_PREFIX)
    app.include_router(bots_router, prefix=API_V1_PREFIX)
    app.include_router(destinations_router, prefix=API_V1_PREFIX)
    # `bot_destinations_router` shares the `/bots` prefix with `bots_router`
    # but registers a different path (`/bots/{id}/destinations`) - see the
    # notes in `modules/destinations/router.py`.
    app.include_router(bot_destinations_router, prefix=API_V1_PREFIX)
    app.include_router(webhooks_router, prefix=API_V1_PREFIX)
    app.include_router(messaging_router, prefix=API_V1_PREFIX)
    app.include_router(message_templates_router, prefix=API_V1_PREFIX)
    app.include_router(billing_router, prefix=API_V1_PREFIX)

    @app.get("/healthz", tags=["health"], summary="Liveness check")
    async def healthz() -> dict:
        """Liveness check for the orchestrator/CI - outside `/api/v1` since

        it isn't part of the API contract, purely operational.
        """
        return success_envelope({"status": "ok"})

    return app


app = create_app()
