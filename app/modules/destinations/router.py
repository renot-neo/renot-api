"""Router module `destinations`.

Endpoints: full Destination CRUD (personal, group, group_thread, channel),
`GET /bots/{id}/destinations`, `PATCH /destinations/{id}/subscription`.

The router's only job: receive the request -> validate via schema -> call
the service -> return the response via the envelope. No business logic here.

Two `APIRouter`s are defined here:
- `router` (prefix `/destinations`): destination CRUD + subscription management.
- `bot_destinations_router` (prefix `/bots`): `GET /bots/{id}/destinations` -
  nested under `bots` in its URL, but its query logic belongs to the
  `destinations` domain (a module can own more than one router when its
  endpoints naturally nest under another module's path, without that module
  having to import `modules/bots` internals).

Authorization goes through `core.deps.require_permission` (the active
tenant in the JWT, same pattern as `modules/bots` - see the notes there).
`destination:view` (read, every role) vs `destination:manage`
(create/update/delete/manage subscriptions, owner+admin only) - see
`modules/organizations/service.py`'s `ROLE_PERMISSIONS`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_tenant, get_db, require_permission
from app.core.pagination import PageParams, PaginatedResponse, pagination_params
from app.core.response import Envelope, success_envelope
from app.modules.destinations import service
from app.modules.destinations.model import BotDestinationSubscription, Destination
from app.modules.destinations.schema import (
    DestinationCreate,
    DestinationResponse,
    DestinationSubscriptionResponse,
    DestinationUpdate,
    SubscriptionResponse,
    SubscriptionUpdate,
)

router = APIRouter(prefix="/destinations", tags=["destinations"])
bot_destinations_router = APIRouter(prefix="/bots", tags=["destinations"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[DestinationResponse],
    summary="Register a new destination",
    description="Manual registration from the dashboard (mainly for the "
    "`channel` type) - immediately creates an `active` subscription to the "
    "given `bot_id`.",
    dependencies=[Depends(require_permission("destination:manage"))],
)
async def create_destination(
    data: DestinationCreate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    destination = await service.create_destination(
        session,
        tenant_id=tenant_id,
        bot_id=data.bot_id,
        type=data.type,
        chat_id=data.chat_id,
        thread_id=data.thread_id,
        title=data.title,
    )
    await session.commit()
    return success_envelope(DestinationResponse.model_validate(destination), request=request)


@router.get(
    "",
    response_model=Envelope[PaginatedResponse[DestinationResponse]],
    summary="List destinations in the active organization",
    description="Paginated via the `page`/`page_size` query params.",
    dependencies=[Depends(require_permission("destination:view"))],
)
async def list_destinations(
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_destinations(session, tenant_id=tenant_id, page_params=page_params)
    data = PaginatedResponse[DestinationResponse].from_page(
        page, DestinationResponse.model_validate
    )
    return success_envelope(data, request=request)


@router.get(
    "/{destination_id}",
    response_model=Envelope[DestinationResponse],
    summary="Destination detail",
    dependencies=[Depends(require_permission("destination:view"))],
)
async def get_destination(
    destination_id: uuid.UUID,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    destination = await service.get_destination(
        session, tenant_id=tenant_id, destination_id=destination_id
    )
    return success_envelope(DestinationResponse.model_validate(destination), request=request)


@router.patch(
    "/{destination_id}",
    response_model=Envelope[DestinationResponse],
    summary="Update a destination",
    description="Updates `title` (a local label). `chat_id`/`thread_id`/`type` "
    "cannot be changed - create a new destination if the Telegram target is different.",
    dependencies=[Depends(require_permission("destination:manage"))],
)
async def update_destination(
    destination_id: uuid.UUID,
    data: DestinationUpdate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    destination = await service.update_destination(
        session, tenant_id=tenant_id, destination_id=destination_id, title=data.title
    )
    await session.commit()
    return success_envelope(DestinationResponse.model_validate(destination), request=request)


@router.delete(
    "/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a destination (soft-delete, cascades to its subscriptions)",
    dependencies=[Depends(require_permission("destination:manage"))],
)
async def delete_destination(
    destination_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_destination(session, tenant_id=tenant_id, destination_id=destination_id)
    await session.commit()


@router.patch(
    "/{destination_id}/subscription",
    response_model=Envelope[SubscriptionResponse],
    summary="Manage a destination's subscription status to a bot",
    description="Manually unsubscribes (`unsubscribed`) or blocks "
    "(`blocked_by_admin`) this destination's subscription to the `bot_id` "
    "given in the body.",
    dependencies=[Depends(require_permission("destination:manage"))],
)
async def update_subscription(
    destination_id: uuid.UUID,
    data: SubscriptionUpdate,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    subscription = await service.update_subscription_status(
        session,
        tenant_id=tenant_id,
        destination_id=destination_id,
        bot_id=data.bot_id,
        status=data.status,
    )
    await session.commit()
    return success_envelope(SubscriptionResponse.model_validate(subscription), request=request)


def _subscription_response(
    pair: tuple[Destination, BotDestinationSubscription],
) -> DestinationSubscriptionResponse:
    destination, subscription = pair
    return DestinationSubscriptionResponse(
        **DestinationResponse.model_validate(destination).model_dump(),
        subscription_status=subscription.status,
    )


@bot_destinations_router.get(
    "/{bot_id}/destinations",
    response_model=Envelope[PaginatedResponse[DestinationSubscriptionResponse]],
    summary="List destinations subscribed to this bot",
    description="Paginated via the `page`/`page_size` query params.",
    dependencies=[Depends(require_permission("destination:view"))],
)
async def list_bot_destinations(
    bot_id: uuid.UUID,
    request: Request,
    page_params: PageParams = Depends(pagination_params),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db),
) -> dict:
    page = await service.list_destinations_for_bot(
        session, tenant_id=tenant_id, bot_id=bot_id, page_params=page_params
    )
    data = PaginatedResponse[DestinationSubscriptionResponse].from_page(
        page, _subscription_response
    )
    return success_envelope(data, request=request)
