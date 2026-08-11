"""Router module `messaging`.

Endpoints: `POST /messages`, `GET /messages/{id}`,
`GET /messages/{id}/status`, and full message-template CRUD.

The router's only job: receive the request -> validate via schema -> call
the service -> return the response via the envelope. No business logic here.

Two `APIRouter`s are defined (same pattern as `modules/destinations`):
- `router` (prefix `/messages`): sending messages + delivery status.
- `templates_router` (prefix `/message-templates`): template CRUD.

Authorization: every endpoint here (including template CRUD) is gated on
`message:send` (the active tenant JWT, via `require_permission` - same
pattern as `modules/bots`) - there's no separate "manage templates"
permission; templates are purely a message-composition helper, so they're
folded into the existing `message:send` permission in `ROLE_PERMISSIONS`
(`modules/organizations/service.py`) rather than adding a new one. Any
MEMBER allowed to `message:send` is also allowed to manage templates - no
separate permission needed. `GET /messages/{id}` & `.../status` use
`log:view` instead (held by the same roles as `message:send`, so it doesn't
change practical access, but is semantically more accurate for "viewing"
vs. "sending").

**Dual-auth** (`POST /messages`, `GET /messages/{id}`, `.../status` ONLY -
NOT `templates_router`, template CRUD stays dashboard-only): besides the
regular dashboard JWT, these 3 endpoints also accept the `X-Bot-Api-Key`
header (per-bot API key for external apps) via
`modules.messaging.deps.require_permission_or_bot_api_key`, instead of the
regular `core.deps.require_permission`. See the `MessagingPrincipal`/
`_assert_bot_access` docstrings for the scoping details (a bot API key can
only access itself).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_tenant, get_db, require_permission
from app.core.pagination import PageParams, PaginatedResponse, pagination_params
from app.core.response import Envelope, success_envelope
from app.modules.messaging import service
from app.modules.messaging.deps import MessagingPrincipal, require_permission_or_bot_api_key
from app.modules.messaging.model import DeliveryStatus
from app.modules.messaging.schema import (
    DeliveryCounts,
    DeliveryLogResponse,
    MessageCreate,
    MessageResponse,
    MessageStatusResponse,
    MessageTemplateCreate,
    MessageTemplateResponse,
    MessageTemplateUpdate,
)

router = APIRouter(prefix="/messages", tags=["messaging"])

# Built once here (not inline in the parameter's `Depends(...)` default) -
# ruff B008 still flags a NESTED call inside `Depends(...)`
# (`require_permission_or_bot_api_key(...)` itself isn't whitelisted like
# `fastapi.Depends` in `pyproject.toml`), same fix pattern as `core/deps.py`.
_require_message_send = Depends(require_permission_or_bot_api_key("message:send"))
_require_log_view = Depends(require_permission_or_bot_api_key("log:view"))
templates_router = APIRouter(prefix="/message-templates", tags=["messaging"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[MessageResponse],
    summary="Send or schedule a message",
    description="Sends a message (text/media/poll) to one or many "
    "destinations at once (broadcast). Leave `scheduled_at` empty to send "
    "immediately - immediate sends are enqueued to Celery (queue "
    "`messaging.send`) right after this request completes; scheduled "
    "messages are dispatched automatically by Celery beat once due. "
    "Authenticate as a dashboard user (JWT Bearer) OR an external app "
    "(per-bot `X-Bot-Api-Key` header) - via API key, `bot_id` in the body "
    "MUST match the bot that owns the key (403 `BOT_NOT_ASSIGNED` otherwise).",
)
async def create_message(
    data: MessageCreate,
    request: Request,
    principal: MessagingPrincipal = _require_message_send,
    session: AsyncSession = Depends(get_db),
) -> dict:
    message, delivery_logs = await service.create_message(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        bot_id=data.bot_id,
        destination_ids=data.destination_ids,
        content_type=data.content_type,
        text=data.text,
        parse_mode=data.parse_mode,
        media_type=data.media_type,
        media_url=data.media_url,
        inline_keyboard=(
            data.inline_keyboard.model_dump(exclude_none=True) if data.inline_keyboard else None
        ),
        poll=data.poll.model_dump(exclude_none=True) if data.poll else None,
        template_id=data.template_id,
        template_variables=data.template_variables,
        scheduled_at=data.scheduled_at,
        restrict_to_bot_id=principal.bot_id,
    )
    # Commit FIRST, then enqueue the Celery task - the worker has a
    # separate DB connection and could start processing faster than this
    # row gets persisted if the order were reversed (see the `service.py`
    # docstring).
    await session.commit()
    service.enqueue_immediate_deliveries(message, delivery_logs)
    return success_envelope(MessageResponse.model_validate(message), request=request)


@router.get(
    "/{message_id}",
    response_model=Envelope[MessageResponse],
    summary="Message detail",
    description="Authenticate as a dashboard user (JWT Bearer) OR an "
    "external app (`X-Bot-Api-Key` header - can only view messages "
    "belonging to that bot itself).",
)
async def get_message(
    message_id: uuid.UUID,
    request: Request,
    principal: MessagingPrincipal = _require_log_view,
    session: AsyncSession = Depends(get_db),
) -> dict:
    message = await service.get_message(
        session,
        tenant_id=principal.tenant_id,
        message_id=message_id,
        actor_user_id=principal.user_id,
        restrict_to_bot_id=principal.bot_id,
    )
    return success_envelope(MessageResponse.model_validate(message), request=request)


@router.get(
    "/{message_id}/status",
    response_model=Envelope[MessageStatusResponse],
    summary="Message delivery status",
    description="Aggregate status + per-destination detail (a polling "
    "alternative for bots without an `outbound_callback_url`). Authenticate "
    "as a dashboard user (JWT Bearer) OR an external app (`X-Bot-Api-Key` "
    "header - can only view status for messages belonging to that bot itself).",
)
async def get_message_status(
    message_id: uuid.UUID,
    request: Request,
    principal: MessagingPrincipal = _require_log_view,
    session: AsyncSession = Depends(get_db),
) -> dict:
    message, logs = await service.get_message_status(
        session,
        tenant_id=principal.tenant_id,
        message_id=message_id,
        actor_user_id=principal.user_id,
        restrict_to_bot_id=principal.bot_id,
    )
    queued = sum(1 for log in logs if log.status == DeliveryStatus.QUEUED)
    sent = sum(1 for log in logs if log.status == DeliveryStatus.SENT)
    failed = sum(1 for log in logs if log.status == DeliveryStatus.FAILED)
    data = MessageStatusResponse(
        message_id=message.id,
        scheduled_at=message.scheduled_at,
        dispatched_at=message.dispatched_at,
        overall_status=service.compute_overall_status(message, logs),
        total_destinations=len(logs),
        counts=DeliveryCounts(queued=queued, sent=sent, failed=failed),
        deliveries=[DeliveryLogResponse.model_validate(log) for log in logs],
    )
    return success_envelope(data, request=request)


@templates_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[MessageTemplateResponse],
    summary="Create a message template",
    dependencies=[Depends(require_permission("message:send"))],
)
async def create_message_template(
    data: MessageTemplateCreate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    template = await service.create_message_template(
        session, tenant_id=tenant_id, name=data.name, body=data.body, parse_mode=data.parse_mode
    )
    await session.commit()
    return success_envelope(MessageTemplateResponse.model_validate(template), request=request)


@templates_router.get(
    "",
    response_model=Envelope[PaginatedResponse[MessageTemplateResponse]],
    summary="List message templates in the active organization",
    description="Paginated via the `page`/`page_size` query params.",
    dependencies=[Depends(require_permission("message:send"))],
)
async def list_message_templates(
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_message_templates(
        session, tenant_id=tenant_id, page_params=page_params
    )
    data = PaginatedResponse[MessageTemplateResponse].from_page(
        page, MessageTemplateResponse.model_validate
    )
    return success_envelope(data, request=request)


@templates_router.get(
    "/{template_id}",
    response_model=Envelope[MessageTemplateResponse],
    summary="Message template detail",
    dependencies=[Depends(require_permission("message:send"))],
)
async def get_message_template(
    template_id: uuid.UUID,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    template = await service.get_message_template(
        session, tenant_id=tenant_id, template_id=template_id
    )
    return success_envelope(MessageTemplateResponse.model_validate(template), request=request)


@templates_router.patch(
    "/{template_id}",
    response_model=Envelope[MessageTemplateResponse],
    summary="Update a message template",
    dependencies=[Depends(require_permission("message:send"))],
)
async def update_message_template(
    template_id: uuid.UUID,
    data: MessageTemplateUpdate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    template = await service.update_message_template(
        session,
        tenant_id=tenant_id,
        template_id=template_id,
        name=data.name,
        body=data.body,
        parse_mode=data.parse_mode,
    )
    await session.commit()
    return success_envelope(MessageTemplateResponse.model_validate(template), request=request)


@templates_router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a message template (soft-delete)",
    dependencies=[Depends(require_permission("message:send"))],
)
async def delete_message_template(
    template_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_message_template(session, tenant_id=tenant_id, template_id=template_id)
    await session.commit()
