"""Router module `webhooks`.

Endpoint: `POST /webhooks/telegram/{bot_id}`.

The router's only job: receive the request -> validate via schema -> call
the service -> return the response via the envelope. No business logic here.

This endpoint is DIFFERENT from other router modules: it's public (called
by the Telegram Bot API, not a dashboard user) - there's NO
`Depends(require_permission(...))` or JWT Bearer at all. Authentication is
via `X-Telegram-Bot-Api-Secret-Token`, validated in
`service.handle_telegram_update` against `Bot.webhook_secret` (not at the
router layer, so the `Bot` lookup stays on a single path through the
`app.modules.bots` service interface).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.response import Envelope, success_envelope
from app.modules.billing import enqueue_event_in
from app.modules.webhooks import service
from app.shared.telegram_types import TelegramUpdate

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/telegram/{bot_id}",
    status_code=status.HTTP_200_OK,
    response_model=Envelope[dict],
    summary="Inbound per-bot Telegram webhook",
    description="Called by the Telegram Bot API (not a dashboard user) for "
    "every new update to this bot - validated via the "
    "`X-Telegram-Bot-Api-Secret-Token` header, NOT a JWT. Handles the "
    "built-in `/start`/`/stop`/`/status`/`/help`/`/about` commands; other "
    "updates are accepted & ignored (still 200 OK), per this MVP's scope.",
    # Hidden from the public OpenAPI schema/docs UI - Telegram calls this,
    # not a customer, so it shouldn't appear as something a customer should
    # call directly. Functionally unchanged: the route still works exactly
    # the same, it just stops appearing in /docs.
    include_in_schema=False,
)
async def receive_telegram_update(
    bot_id: uuid.UUID,
    update: TelegramUpdate,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = await service.handle_telegram_update(
        session, bot_id=bot_id, secret_token=x_telegram_bot_api_secret_token, update=update
    )
    await session.commit()
    # Recorded as a UsageEvent event_in - AFTER commit (the router->DB->Celery
    # pattern), see the `service.handle_telegram_update` docstring.
    enqueue_event_in(tenant_id=tenant_id, bot_id=bot_id)
    return success_envelope({"status": "ok"}, request=request)
