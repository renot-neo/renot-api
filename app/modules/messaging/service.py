"""Business logic for the `messaging` module.

Scope: sending messages, templates, scheduling, and delivery tracking.

This is the layer the router calls into - the only place business logic
belongs. Bots/destinations are validated via the public service interfaces
`app.modules.bots`/`app.modules.destinations` - this module MUST NOT
`from app.modules.bots.model import Bot` (or `destinations.model`) directly.

**"Assigned bot" scoping (`_assert_bot_access`)**: the MEMBER role can only
`message:send`/`log:view` on a bot assigned to them
(`app.modules.bots.is_assigned`, `BotAssignment` - see
`modules/bots/model.py`). OWNER/ADMIN aren't restricted. This check needs
the actor's role, so this module also imports `app.modules.organizations`
(`get_membership`/`OrganizationRole`) - the router's `require_permission`
only checks the generic per-role permission, it has no notion of per-bot
assignment.

**Celery dispatch pattern (important)**: `create_message` ONLY prepares DB
rows (`Message` + `DeliveryLog` with status `queued`) and does NOT enqueue
any task - enqueueing is done separately by `enqueue_immediate_deliveries`
(called by `router.py` AFTER `session.commit()`) or by
`dispatch_due_scheduled_messages` (called by
`tasks.dispatch_scheduled_message`, also after commit). Reason: the Celery
worker has its own DB connection (a separate process) - if a task were
enqueued BEFORE the transaction commits, the worker could start processing
before the row is actually persisted (a race condition), breaking the
idempotency this system requires.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams
from app.modules.bots import get_bot, is_assigned
from app.modules.destinations import get_destination, is_actively_subscribed
from app.modules.messaging.exceptions import (
    BotNotAssignedError,
    DestinationNotSubscribedError,
    MessageNotFoundError,
    MessageTemplateNotFoundError,
    ScheduledAtInPastError,
    TemplateVariableMissingError,
)
from app.modules.messaging.model import (
    DeliveryLog,
    DeliveryStatus,
    MediaType,
    Message,
    MessageContentType,
    MessageTemplate,
    OverallStatus,
    ParseMode,
)
from app.modules.messaging.repository import (
    DeliveryLogRepository,
    MessageRepository,
    MessageTemplateRepository,
)
from app.modules.organizations import OrganizationRole, get_membership

_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


async def _assert_bot_access(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    bot_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    restrict_to_bot_id: uuid.UUID | None = None,
) -> None:
    """Scoping: the MEMBER role can only `message:send`/`log:view` on a bot

    assigned to them (`modules.bots.is_assigned`). OWNER/ADMIN aren't
    restricted - they already have full access via the router's regular
    `require_permission`. `membership is None` (should never actually
    happen - `require_permission` already guarantees the actor is a member
    of the active tenant before reaching here) is treated the same as
    MEMBER (still checked against assignment) rather than as a separate
    error - stays safe by default.

    `restrict_to_bot_id`/`actor_user_id=None` - the dual-auth per-bot API
    key path (see `modules.messaging.deps.MessagingPrincipal`): the request
    was authenticated via `X-Bot-Api-Key`, NOT a user JWT, so there's no
    membership/role to check an assignment against at all - the bot's own
    identity (from its API key) is already the tightest possible scope, it
    just needs to match `restrict_to_bot_id == bot_id`. `BotNotAssignedError`
    is reused for this mismatch too (not a new exception) - the semantics
    are identical: "this credential isn't authorized for this bot", just a
    different credential source (assignment vs. API key).
    """
    if restrict_to_bot_id is not None and restrict_to_bot_id != bot_id:
        raise BotNotAssignedError()
    if actor_user_id is None:
        return

    membership = await get_membership(session, user_id=actor_user_id, organization_id=tenant_id)
    if membership is not None and membership.role in (
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
    ):
        return
    if not await is_assigned(
        session=session, tenant_id=tenant_id, bot_id=bot_id, user_id=actor_user_id
    ):
        raise BotNotAssignedError()


def render_template_text(body: str, variables: dict[str, str] | None) -> str:
    """Substitutes `{{variable_name}}` in `body` with `variables` - raises

    `TemplateVariableMissingError` if a placeholder has no value (prevents a
    half-rendered message from being broadcast to Telegram).
    """
    variables = variables or {}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise TemplateVariableMissingError(f"Missing template variable: `{key}`.")
        return variables[key]

    return _PLACEHOLDER_RE.sub(_replace, body)


def compute_overall_status(message: Message, logs: list[DeliveryLog]) -> OverallStatus:
    """The aggregate status used by `GET /messages/{id}/status` - computed

    on-the-fly from `Message.scheduled_at`/`dispatched_at` + `DeliveryLog`
    status counts, not stored as its own column.
    """
    if message.scheduled_at is not None and message.dispatched_at is None:
        return "scheduled"

    total = len(logs)
    if total == 0:
        return "sent"
    queued = sum(1 for log in logs if log.status == DeliveryStatus.QUEUED)
    sent = sum(1 for log in logs if log.status == DeliveryStatus.SENT)
    failed = sum(1 for log in logs if log.status == DeliveryStatus.FAILED)

    if queued == total:
        return "queued"
    if queued > 0:
        return "in_progress"
    if failed == 0:
        return "sent"
    if sent == 0:
        return "failed"
    return "partially_failed"


async def create_message_template(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    body: str,
    parse_mode: ParseMode | None,
) -> MessageTemplate:
    return await MessageTemplateRepository(session).create(
        tenant_id=tenant_id, name=name, body=body, parse_mode=parse_mode
    )


async def get_message_template(
    session: AsyncSession, *, tenant_id: uuid.UUID, template_id: uuid.UUID
) -> MessageTemplate:
    template = await MessageTemplateRepository(session).get_active(
        tenant_id=tenant_id, template_id=template_id
    )
    if template is None:
        raise MessageTemplateNotFoundError()
    return template


async def list_message_templates(
    session: AsyncSession, *, tenant_id: uuid.UUID, page_params: PageParams
) -> Page[MessageTemplate]:
    return await MessageTemplateRepository(session).list_active(
        tenant_id=tenant_id, params=page_params
    )


async def update_message_template(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    name: str | None = None,
    body: str | None = None,
    parse_mode: ParseMode | None = None,
) -> MessageTemplate:
    template = await get_message_template(session, tenant_id=tenant_id, template_id=template_id)
    if name is not None:
        template.name = name
    if body is not None:
        template.body = body
    if parse_mode is not None:
        template.parse_mode = parse_mode
    await session.flush()
    return template


async def delete_message_template(
    session: AsyncSession, *, tenant_id: uuid.UUID, template_id: uuid.UUID
) -> None:
    template = await get_message_template(session, tenant_id=tenant_id, template_id=template_id)
    await MessageTemplateRepository(session).soft_delete(template)


async def create_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    bot_id: uuid.UUID,
    destination_ids: list[uuid.UUID],
    content_type: MessageContentType,
    text: str | None,
    parse_mode: ParseMode | None,
    media_type: MediaType | None,
    media_url: str | None,
    inline_keyboard: dict | None,
    poll: dict | None,
    template_id: uuid.UUID | None,
    template_variables: dict[str, str] | None,
    scheduled_at: datetime | None,
    restrict_to_bot_id: uuid.UUID | None = None,
) -> tuple[Message, list[DeliveryLog]]:
    """Validates the bot + each destination (must be actively subscribed to

    this bot, see `destinations.is_actively_subscribed`), renders the
    template if one is used, then persists the `Message` + a `DeliveryLog`
    (`queued`) per destination. Does NOT enqueue a Celery task - see the
    module docstring.

    `actor_user_id=None`/`restrict_to_bot_id` - see the `_assert_bot_access`
    docstring (the `X-Bot-Api-Key` path).
    """
    await get_bot(session, tenant_id=tenant_id, bot_id=bot_id)
    await _assert_bot_access(
        session,
        tenant_id=tenant_id,
        bot_id=bot_id,
        actor_user_id=actor_user_id,
        restrict_to_bot_id=restrict_to_bot_id,
    )

    for destination_id in destination_ids:
        await get_destination(session, tenant_id=tenant_id, destination_id=destination_id)
        if not await is_actively_subscribed(
            session, tenant_id=tenant_id, bot_id=bot_id, destination_id=destination_id
        ):
            raise DestinationNotSubscribedError()

    resolved_text = text
    resolved_parse_mode = parse_mode
    if template_id is not None:
        template = await get_message_template(session, tenant_id=tenant_id, template_id=template_id)
        resolved_text = render_template_text(template.body, template_variables)
        if resolved_parse_mode is None:
            resolved_parse_mode = template.parse_mode

    if scheduled_at is not None and scheduled_at <= datetime.now(UTC):
        raise ScheduledAtInPastError()

    message = await MessageRepository(session).create(
        tenant_id=tenant_id,
        bot_id=bot_id,
        template_id=template_id,
        content_type=content_type,
        text=resolved_text,
        parse_mode=resolved_parse_mode,
        media_type=media_type,
        media_url=media_url,
        inline_keyboard=inline_keyboard,
        poll=poll,
        scheduled_at=scheduled_at,
    )
    delivery_logs = await DeliveryLogRepository(session).bulk_create_queued(
        tenant_id=tenant_id, message_id=message.id, destination_ids=destination_ids
    )
    return message, delivery_logs


def enqueue_immediate_deliveries(message: Message, delivery_logs: list[DeliveryLog]) -> None:
    """Triggers `send_message_to_destination` (queue `messaging.send`) per

    `DeliveryLog` - called by `router.create_message` AFTER
    `session.commit()` (see the module docstring). A no-op for a scheduled
    message (`message.scheduled_at` is set) - that becomes
    `dispatch_due_scheduled_messages`'s responsibility once it's due.
    """
    if message.scheduled_at is not None:
        return
    from app.modules.messaging.tasks import send_message_to_destination

    for log in delivery_logs:
        send_message_to_destination.delay(delivery_log_id=str(log.id))


async def get_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    restrict_to_bot_id: uuid.UUID | None = None,
) -> Message:
    message = await MessageRepository(session).get_active(
        tenant_id=tenant_id, message_id=message_id
    )
    if message is None:
        raise MessageNotFoundError()
    await _assert_bot_access(
        session,
        tenant_id=tenant_id,
        bot_id=message.bot_id,
        actor_user_id=actor_user_id,
        restrict_to_bot_id=restrict_to_bot_id,
    )
    return message


async def get_message_status(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    restrict_to_bot_id: uuid.UUID | None = None,
) -> tuple[Message, list[DeliveryLog]]:
    message = await get_message(
        session,
        tenant_id=tenant_id,
        message_id=message_id,
        actor_user_id=actor_user_id,
        restrict_to_bot_id=restrict_to_bot_id,
    )
    logs = await DeliveryLogRepository(session).list_for_message(
        tenant_id=tenant_id, message_id=message_id
    )
    return message, logs


async def dispatch_due_scheduled_messages(
    session: AsyncSession,
) -> list[tuple[Message, list[DeliveryLog]]]:
    """Called by `tasks.dispatch_scheduled_message` (Celery beat, queue

    `messaging.scheduled`) - finds scheduled `Message`s that are due
    (`scheduled_at <= now`) but not yet dispatched, marks `dispatched_at`,
    and returns their `(message, delivery_logs)` so the caller can enqueue
    the send task AFTER `session.commit()` (same pattern as
    `enqueue_immediate_deliveries`, see the module docstring).

    Known limitation (flagged, not an oversight): if the worker process
    crashes right after the commit here but before it gets to enqueue the
    send task, that message ends up with `dispatched_at` set but never
    actually sent - it needs manual recovery (there's no admin
    endpoint/command for that yet). Accepted as an MVP trade-off rather than
    building a full outbox pattern, to keep scope from expanding.
    """
    due_messages = await MessageRepository(session).list_due_for_dispatch(now=datetime.now(UTC))
    dispatched: list[tuple[Message, list[DeliveryLog]]] = []
    for message in due_messages:
        logs = await DeliveryLogRepository(session).list_for_message(
            tenant_id=message.tenant_id, message_id=message.id
        )
        await MessageRepository(session).mark_dispatched(message)
        dispatched.append((message, logs))
    return dispatched


async def cascade_delete_pending_messages(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Called by `modules/organizations.delete_organization` (the

    organization-delete cascade) - soft-deletes this tenant's `Message`s
    that have NOT been dispatched yet (`dispatched_at IS NULL`), NOT the
    entire `Message` history. This is REQUIRED (not optional/okay-to-orphan)
    because `list_due_for_dispatch` above scans ACROSS ALL TENANTS with no
    active-organization filter - if a scheduled message were left active,
    `dispatch_due_scheduled_messages` (Celery beat `messaging.scheduled`)
    would still send it even after the org has been deleted.

    Messages that have ALREADY been dispatched (history) are deliberately
    NOT included in this cascade - their volume can be large (unlike
    `Bot`/`BotAssignment` per org), and there's no access path that bypasses
    the "organization is active" guard (`core.deps.require_permission`) to
    read them - it's enough for them to become orphaned/inactive once
    `Organization.deleted_at` is set.
    """
    messages = MessageRepository(session)
    for message in await messages.list_pending_for_tenant(tenant_id=tenant_id):
        await messages.soft_delete(message)


async def purge_delivery_logs_batch(
    session: AsyncSession, *, tenant_id: uuid.UUID, before: datetime, batch_size: int
) -> int:
    """Hard-deletes ONE batch of this tenant's `DeliveryLog`s with

    `created_at < before` - exposed via `__init__.py` so `modules/billing`
    (where the retention policy is centralized in
    `billing.Plan.retention_days`, see its docstring) can call this without
    importing the `DeliveryLog` model class directly. The parent `Message`
    is NOT deleted/touched - this purge is purely cleaning up the
    operational `DeliveryLog` ledger; `Message` still follows the regular
    `TenantScopedBase` soft-delete. Side effect: `GET /messages/{id}/status`
    for an old message will show fewer/zero per-destination entries once
    its logs age past retention. The caller (`billing/tasks.py`) loops this
    batch until exhausted, committing between batches.
    """
    return await DeliveryLogRepository(session).delete_before(
        tenant_id=tenant_id, before=before, batch_size=batch_size
    )
