"""Unit tests for `app.core.exceptions`.

Regression test: found during live end-to-end verification of
`modules/messaging` - Pydantic v2 embeds the ORIGINAL exception object
(`ValueError`) in `ctx.error` on `RequestValidationError.errors()`'s output
for errors raised manually from a custom `@model_validator` (e.g.
`MessageCreate._validate_content_fields`). Before the fix, this handler sent
the raw `exc.errors()` to `JSONResponse` -> `json.dumps` blew up with
`TypeError: Object of type ValueError is not JSON serializable` -> the
client got a generic 500 instead of a 422 with clear validation details.
Tested with a minimal FastAPI app + `httpx.ASGITransport`, not the real
application endpoint - enough to test this handler's own behavior without DB/network.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, model_validator

from app.core.exceptions import register_exception_handlers


class _Payload(BaseModel):
    content_type: str
    value: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> _Payload:
        if self.content_type == "needs_value" and not self.value:
            raise ValueError("`value` is required when `content_type=needs_value`.")
        return self


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    async def echo(payload: _Payload) -> dict:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_validation_error_from_custom_model_validator_returns_422_not_500() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", json={"content_type": "needs_value"})

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # `ctx.error` (the original exception object) must already be
    # normalized to a string by `jsonable_encoder`, not cause
    # `response.json()` to fail parsing / make the request itself 500 first.
    assert body["error"]["details"]


@pytest.mark.asyncio
async def test_valid_payload_passes_through_custom_model_validator() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/echo", json={"content_type": "needs_value", "value": "present"}
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
