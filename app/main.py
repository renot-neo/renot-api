"""FastAPI entrypoint - registers routers & middleware.

URL versioning: every domain route is prefixed `/api/v1/...`. Router
modules are registered here once their concrete endpoints are available.
"""

from __future__ import annotations

import logging
import sys
import tomllib
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

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

_PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_FAVICON_URL = "/static/favicon.ico"


def _read_app_version(pyproject_path: Path) -> str:
    """Read `[project].version` from `pyproject.toml` at runtime, so this

    stays in sync with the version release-please bumps automatically
    (`release-type: python`) instead of drifting from a separate hardcoded
    string. Falls back to `"0.0.0"` if the file is missing/malformed rather
    than crashing app startup - a wrong-but-present version string is
    better than a broken deployment.
    """
    try:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        return data["project"]["version"]
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError):
        return "0.0.0"


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

    # Interactive docs UI (/docs, /redoc) is disabled in production - reduces
    # casual discovery (no browsable Swagger/ReDoc UI at a guessable path).
    # Note this does NOT hide the schema itself: `openapi_url` stays enabled
    # in every environment by design, so `/api/v1/openapi.json` is still
    # reachable directly.
    docs_enabled = settings.environment != "production"
    # docs_url/redoc_url stay None unconditionally here, even when
    # docs_enabled - FastAPI's own built-in /docs and /redoc routes (added
    # automatically by this constructor whenever these are non-None) always
    # point at FastAPI's own hardcoded favicon with no override hook, so
    # branding this app's docs UI (below) requires suppressing them and
    # registering fully custom routes at the same paths instead.
    app = FastAPI(
        title=settings.app_name,
        version=_read_app_version(_PYPROJECT_PATH),
        docs_url=None,
        redoc_url=None,
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

    # Static assets (currently just the favicon) - mounted unconditionally,
    # in every environment. Serving one small icon file isn't part of the
    # docs-UI discovery concern the docs_enabled gate above exists for.
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        """Conventional root-level favicon browsers request automatically

        for any page (e.g. /healthz opened directly) - separate from the
        /docs and /redoc pages below, which reference `_FAVICON_URL`
        explicitly rather than relying on this route.
        """
        return FileResponse(_STATIC_DIR / "favicon.ico")

    if docs_enabled:

        @app.get("/docs", include_in_schema=False)
        async def swagger_ui_html() -> HTMLResponse:
            return get_swagger_ui_html(
                # `app.openapi_url` is typed `str | None` (FastAPI allows
                # disabling the schema entirely) even though this app always
                # passes a concrete value above - use that same literal
                # directly rather than reading the attribute back, so mypy
                # doesn't need a redundant None-check for a case that can't
                # happen here.
                openapi_url=f"{API_V1_PREFIX}/openapi.json",
                title=f"{app.title} - Swagger UI",
                swagger_favicon_url=_FAVICON_URL,
            )

        @app.get("/redoc", include_in_schema=False)
        async def redoc_html() -> HTMLResponse:
            return get_redoc_html(
                openapi_url=f"{API_V1_PREFIX}/openapi.json",  # see swagger_ui_html above
                title=f"{app.title} - ReDoc",
                redoc_favicon_url=_FAVICON_URL,
            )

    @app.get("/healthz", tags=["health"], summary="Liveness check")
    async def healthz() -> dict:
        """Liveness check for the orchestrator/CI - outside `/api/v1` since

        it isn't part of the API contract, purely operational.
        """
        return success_envelope({"status": "ok"})

    return app


app = create_app()
