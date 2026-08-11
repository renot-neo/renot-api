"""Envelope response builder.

Success shape:
    {"success": true, "data": ..., "meta": {...}, "error": null}

Error shape:
    {"success": false, "data": null, "meta": {...}, "error": {"code", "message", "details"}}

`Envelope[T]` lets a router set an explicit `response_model=Envelope[XResponse]`
while still wrapping the data in the envelope - success endpoints MUST
`return success_envelope(...)`, never a raw `return <schema>` (the error
path already goes through `core/exceptions.py`, not through `response_model`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from starlette.requests import Request


class Meta(BaseModel):
    request_id: str
    timestamp: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[Any] = []


class Envelope[T](BaseModel):
    success: bool
    data: T | None = None
    meta: Meta
    error: ErrorDetail | None = None


def _meta(request: Request | None) -> dict[str, Any]:
    request_id = getattr(request.state, "request_id", None) if request is not None else None
    return {
        "request_id": request_id or str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
    }


def success_envelope(data: Any = None, *, request: Request | None = None) -> dict[str, Any]:
    return {"success": True, "data": data, "meta": _meta(request), "error": None}


def error_envelope(
    *,
    request: Request | None,
    code: str,
    message: str,
    details: Sequence[Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "meta": _meta(request),
        "error": {"code": code, "message": message, "details": list(details) if details else []},
    }
