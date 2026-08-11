"""Router module `bots`.

Endpoints: `POST /bots`, `GET /bots`, `GET /bots/{id}`, `PATCH /bots/{id}`,
`DELETE /bots/{id}`, `POST /bots/{id}/regenerate-key`,
`PATCH /bots/{id}/subscription-policy`, plus assignment management:
`POST/GET /bots/{id}/assignments`, `DELETE /bots/{id}/assignments/{user_id}`
(backing "MEMBER can only access bots assigned to them" - see `service.py`
for details).

The router's only job: receive the request -> validate via schema -> call
the service -> return the response via the envelope. No business logic here.

Authorization goes through `core.deps.require_permission` (the active
tenant in the JWT, via `get_current_tenant`), NOT an organization ID from
the path like `modules/organizations` uses - bot endpoints don't carry an
organization ID in the path. `bot:view` (read) vs `bot:manage`
(create/update/delete, including managing assignments - owner/admin only) -
see `modules/organizations/service.py`'s `ROLE_PERMISSIONS`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_tenant, get_db, require_permission
from app.core.pagination import PageParams, PaginatedResponse, pagination_params
from app.core.response import Envelope, success_envelope
from app.modules.bots import service
from app.modules.bots.model import Bot
from app.modules.bots.schema import (
    BotAssignmentCreate,
    BotAssignmentResponse,
    BotCreate,
    BotResponse,
    BotSecretResponse,
    BotSubscriptionPolicyUpdate,
    BotUpdate,
)

router = APIRouter(prefix="/bots", tags=["bots"])


def _secret_response(bot: Bot, api_key: str) -> BotSecretResponse:
    base = BotResponse.model_validate(bot)
    return BotSecretResponse(**base.model_dump(), api_key=api_key)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[BotSecretResponse],
    summary="Register a new Telegram bot",
    description="Validates the BotFather token via `getMe`, saves the bot "
    "record, and automatically `setWebhook`s to the platform with a unique "
    "secret token. The REST API key is shown ONLY in this response - save "
    "it now, it cannot be viewed again.",
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def create_bot(
    data: BotCreate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    bot, api_key = await service.register_bot(
        session,
        tenant_id=tenant_id,
        name=data.name,
        token=data.token,
        outbound_callback_url=data.outbound_callback_url,
    )
    await session.commit()
    return success_envelope(_secret_response(bot, api_key), request=request)


@router.get(
    "",
    response_model=Envelope[PaginatedResponse[BotResponse]],
    summary="List bots in the active organization",
    description="Lists the bots in the currently active organization (JWT "
    "tenant context), paginated via the `page`/`page_size` query params.",
    dependencies=[Depends(require_permission("bot:view"))],
)
async def list_bots(
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_bots(session, tenant_id=tenant_id, page_params=page_params)
    data = PaginatedResponse[BotResponse].from_page(page, BotResponse.model_validate)
    return success_envelope(data, request=request)


@router.get(
    "/{bot_id}",
    response_model=Envelope[BotResponse],
    summary="Bot detail",
    dependencies=[Depends(require_permission("bot:view"))],
)
async def get_bot(
    bot_id: uuid.UUID,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    bot = await service.get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    return success_envelope(BotResponse.model_validate(bot), request=request)


@router.patch(
    "/{bot_id}",
    response_model=Envelope[BotResponse],
    summary="Update a bot",
    description="Updates `name` and/or `outbound_callback_url`. A field "
    "that's omitted (or `null`) is left unchanged; send "
    '`outbound_callback_url: ""` (empty string) to clear it.',
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def update_bot(
    bot_id: uuid.UUID,
    data: BotUpdate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    bot = await service.update_bot(
        session,
        tenant_id=tenant_id,
        bot_id=bot_id,
        name=data.name,
        outbound_callback_url=data.outbound_callback_url,
    )
    await session.commit()
    return success_envelope(BotResponse.model_validate(bot), request=request)


@router.post(
    "/{bot_id}/regenerate-key",
    response_model=Envelope[BotSecretResponse],
    summary="Regenerate a bot's API key",
    description="Issues a new REST API key for this bot (the old key is "
    "immediately invalidated). The plaintext key is shown ONLY in this response.",
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def regenerate_api_key(
    bot_id: uuid.UUID,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    bot, api_key = await service.regenerate_api_key(session, tenant_id=tenant_id, bot_id=bot_id)
    await session.commit()
    return success_envelope(_secret_response(bot, api_key), request=request)


@router.patch(
    "/{bot_id}/subscription-policy",
    response_model=Envelope[BotResponse],
    summary="Open/close a bot's subscription policy",
    description="Toggles `webhook_enabled` - while closed, `/start` from a "
    "new chat does not automatically create a subscription.",
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def update_subscription_policy(
    bot_id: uuid.UUID,
    data: BotSubscriptionPolicyUpdate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    bot = await service.update_subscription_policy(
        session, tenant_id=tenant_id, bot_id=bot_id, webhook_enabled=data.webhook_enabled
    )
    await session.commit()
    return success_envelope(BotResponse.model_validate(bot), request=request)


@router.delete(
    "/{bot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a bot (soft-delete)",
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def delete_bot(
    bot_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    await session.commit()


@router.post(
    "/{bot_id}/assignments",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[BotAssignmentResponse],
    summary="Assign a member to a bot",
    description="Grants the MEMBER role `message:send`/`log:view` access to "
    "this bot (MEMBER can only access bots assigned to them). `user_id` "
    "must already be a member of the active organization. Owner/admin don't "
    "need to be assigned - they already have full access to every bot in the org.",
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def assign_bot(
    bot_id: uuid.UUID,
    data: BotAssignmentCreate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    assignment = await service.assign_bot(
        session, tenant_id=tenant_id, bot_id=bot_id, user_id=data.user_id
    )
    await session.commit()
    return success_envelope(BotAssignmentResponse.model_validate(assignment), request=request)


@router.get(
    "/{bot_id}/assignments",
    response_model=Envelope[PaginatedResponse[BotAssignmentResponse]],
    summary="List members assigned to this bot",
    description="Paginated via the `page`/`page_size` query params.",
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def list_bot_assignments(
    bot_id: uuid.UUID,
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_bot_assignments(
        session, tenant_id=tenant_id, bot_id=bot_id, page_params=page_params
    )
    data = PaginatedResponse[BotAssignmentResponse].from_page(
        page, BotAssignmentResponse.model_validate
    )
    return success_envelope(data, request=request)


@router.delete(
    "/{bot_id}/assignments/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unassign a member from a bot",
    dependencies=[Depends(require_permission("bot:manage"))],
)
async def unassign_bot(
    bot_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    await service.unassign_bot(session, tenant_id=tenant_id, bot_id=bot_id, user_id=user_id)
    await session.commit()
