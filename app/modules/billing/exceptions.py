"""Custom exceptions for the `billing` module, subclassing `AppException`

(`app.core.exceptions`).

Scope: plans, usage metering, and retention.
"""

from __future__ import annotations

from fastapi import status

from app.core.exceptions import AppException


class PlanNotFoundError(AppException):
    code = "PLAN_NOT_FOUND"
    message = "Plan not found."
    status_code = status.HTTP_404_NOT_FOUND


class DefaultPlanNotConfiguredError(AppException):
    """No `Plan.is_default=True` row exists in the DB - should never happen

    outside an environment where migrations haven't been fully run (see the
    data migration in `alembic/versions/` that seeds the "Default" plan).
    500, not 4xx - this is a server configuration error, not bad user input.
    """

    code = "DEFAULT_PLAN_NOT_CONFIGURED"
    message = "No default plan is configured on this server."
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
