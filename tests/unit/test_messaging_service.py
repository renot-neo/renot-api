"""Unit tests for `app.modules.messaging.service`.

Pure logic - the repository & other modules' service interfaces
(`bots.get_bot`, `destinations.get_destination`/`is_actively_subscribed`)
are mocked, no real DB/network.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.modules.bots.exceptions import BotNotFoundError
from app.modules.destinations.exceptions import DestinationNotFoundError
from app.modules.messaging import service
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
    Message,
    MessageContentType,
    MessageTemplate,
    ParseMode,
)
from app.modules.organizations import OrganizationRole

# `_assert_bot_access` ("MEMBER can only access assigned bots") needs
# `get_membership` - patched by default to return OWNER (bypassing the
# assignment check) so tests that AREN'T testing the scoping itself don't
# need to know assignment details. The scoping tests themselves are in the
# "_assert_bot_access" section below, patching `get_membership`/`is_assigned`
# explicitly per case (MEMBER assigned/not, etc.).
_OWNER_MEMBERSHIP = Mock(role=OrganizationRole.OWNER)


def _message(**overrides: object) -> Message:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "bot_id": uuid.uuid4(),
        "template_id": None,
        "content_type": MessageContentType.TEXT,
        "text": "hello",
        "parse_mode": None,
        "media_type": None,
        "media_url": None,
        "inline_keyboard": None,
        "poll": None,
        "scheduled_at": None,
        "dispatched_at": None,
    }
    defaults.update(overrides)
    return Message(**defaults)  # type: ignore[arg-type]


def _delivery_log(**overrides: object) -> DeliveryLog:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "destination_id": uuid.uuid4(),
        "status": DeliveryStatus.QUEUED,
        "telegram_message_id": None,
        "error_reason": None,
        "sent_at": None,
    }
    defaults.update(overrides)
    return DeliveryLog(**defaults)  # type: ignore[arg-type]


def _template(**overrides: object) -> MessageTemplate:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "name": "Welcome",
        "body": "Hi {{name}}, welcome to {{place}}!",
        "parse_mode": None,
    }
    defaults.update(overrides)
    return MessageTemplate(**defaults)  # type: ignore[arg-type]


# --- render_template_text ---


def test_render_template_text_substitutes_all_placeholders() -> None:
    result = service.render_template_text(
        "Hi {{name}}, welcome to {{place}}!", {"name": "Alice", "place": "Renot"}
    )
    assert result == "Hi Alice, welcome to Renot!"


def test_render_template_text_raises_when_variable_missing() -> None:
    with pytest.raises(TemplateVariableMissingError):
        service.render_template_text("Hi {{name}}!", {})


def test_render_template_text_no_placeholders_returns_body_unchanged() -> None:
    assert service.render_template_text("Plain body, no placeholders.", None) == (
        "Plain body, no placeholders."
    )


# --- compute_overall_status ---


def test_compute_overall_status_scheduled_when_not_yet_dispatched() -> None:
    message = _message(scheduled_at=datetime.now(UTC) + timedelta(hours=1), dispatched_at=None)
    assert service.compute_overall_status(message, []) == "scheduled"


def test_compute_overall_status_queued_when_all_logs_queued() -> None:
    message = _message()
    logs = [
        _delivery_log(status=DeliveryStatus.QUEUED),
        _delivery_log(status=DeliveryStatus.QUEUED),
    ]
    assert service.compute_overall_status(message, logs) == "queued"


def test_compute_overall_status_in_progress_when_mixed_queued_and_sent() -> None:
    message = _message()
    logs = [_delivery_log(status=DeliveryStatus.SENT), _delivery_log(status=DeliveryStatus.QUEUED)]
    assert service.compute_overall_status(message, logs) == "in_progress"


def test_compute_overall_status_sent_when_all_sent() -> None:
    message = _message()
    logs = [_delivery_log(status=DeliveryStatus.SENT), _delivery_log(status=DeliveryStatus.SENT)]
    assert service.compute_overall_status(message, logs) == "sent"


def test_compute_overall_status_failed_when_all_failed() -> None:
    message = _message()
    logs = [
        _delivery_log(status=DeliveryStatus.FAILED),
        _delivery_log(status=DeliveryStatus.FAILED),
    ]
    assert service.compute_overall_status(message, logs) == "failed"


def test_compute_overall_status_partially_failed_when_mixed_sent_and_failed() -> None:
    message = _message()
    logs = [_delivery_log(status=DeliveryStatus.SENT), _delivery_log(status=DeliveryStatus.FAILED)]
    assert service.compute_overall_status(message, logs) == "partially_failed"


# --- message template CRUD ---


@pytest.mark.asyncio
async def test_create_message_template_delegates_to_repository() -> None:
    created = _template()
    with patch("app.modules.messaging.service.MessageTemplateRepository") as repo_cls:
        repo_cls.return_value.create = AsyncMock(return_value=created)

        result = await service.create_message_template(
            AsyncMock(), tenant_id=uuid.uuid4(), name="Welcome", body="Hi {{name}}", parse_mode=None
        )

        assert result is created


@pytest.mark.asyncio
async def test_get_message_template_raises_when_not_found() -> None:
    with patch("app.modules.messaging.service.MessageTemplateRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(MessageTemplateNotFoundError):
            await service.get_message_template(
                AsyncMock(), tenant_id=uuid.uuid4(), template_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_update_message_template_only_changes_provided_fields() -> None:
    existing = _template(name="Old", body="Old body", parse_mode=None)
    with patch("app.modules.messaging.service.MessageTemplateRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=existing)

        updated = await service.update_message_template(
            AsyncMock(), tenant_id=uuid.uuid4(), template_id=existing.id, name="New"
        )

        assert updated.name == "New"
        assert updated.body == "Old body"


@pytest.mark.asyncio
async def test_delete_message_template_calls_soft_delete() -> None:
    existing = _template()
    with patch("app.modules.messaging.service.MessageTemplateRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.get_active = AsyncMock(return_value=existing)
        repo.soft_delete = AsyncMock()

        await service.delete_message_template(
            AsyncMock(), tenant_id=uuid.uuid4(), template_id=existing.id
        )

        repo.soft_delete.assert_awaited_once_with(existing)


# --- create_message ---


@pytest.mark.asyncio
async def test_create_message_success_creates_message_and_queued_delivery_logs() -> None:
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    destination_ids = [uuid.uuid4(), uuid.uuid4()]
    created_message = _message(tenant_id=tenant_id, bot_id=bot_id)
    created_logs = [
        _delivery_log(message_id=created_message.id, destination_id=d) for d in destination_ids
    ]

    with (
        patch("app.modules.messaging.service.get_bot", AsyncMock(return_value=object())),
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=_OWNER_MEMBERSHIP),
        ),
        patch("app.modules.messaging.service.get_destination", AsyncMock(return_value=object())),
        patch("app.modules.messaging.service.is_actively_subscribed", AsyncMock(return_value=True)),
        patch("app.modules.messaging.service.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.service.DeliveryLogRepository") as log_repo_cls,
    ):
        msg_repo_cls.return_value.create = AsyncMock(return_value=created_message)
        log_repo_cls.return_value.bulk_create_queued = AsyncMock(return_value=created_logs)

        message, logs = await service.create_message(
            AsyncMock(),
            tenant_id=tenant_id,
            actor_user_id=uuid.uuid4(),
            bot_id=bot_id,
            destination_ids=destination_ids,
            content_type=MessageContentType.TEXT,
            text="hello",
            parse_mode=None,
            media_type=None,
            media_url=None,
            inline_keyboard=None,
            poll=None,
            template_id=None,
            template_variables=None,
            scheduled_at=None,
        )

        assert message is created_message
        assert logs == created_logs
        log_repo_cls.return_value.bulk_create_queued.assert_awaited_once_with(
            tenant_id=tenant_id, message_id=created_message.id, destination_ids=destination_ids
        )


@pytest.mark.asyncio
async def test_create_message_propagates_bot_not_found() -> None:
    with patch("app.modules.messaging.service.get_bot", AsyncMock(side_effect=BotNotFoundError())):
        with pytest.raises(BotNotFoundError):
            await service.create_message(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                bot_id=uuid.uuid4(),
                destination_ids=[uuid.uuid4()],
                content_type=MessageContentType.TEXT,
                text="hi",
                parse_mode=None,
                media_type=None,
                media_url=None,
                inline_keyboard=None,
                poll=None,
                template_id=None,
                template_variables=None,
                scheduled_at=None,
            )


@pytest.mark.asyncio
async def test_create_message_propagates_destination_not_found() -> None:
    with (
        patch("app.modules.messaging.service.get_bot", AsyncMock(return_value=object())),
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=_OWNER_MEMBERSHIP),
        ),
        patch(
            "app.modules.messaging.service.get_destination",
            AsyncMock(side_effect=DestinationNotFoundError()),
        ),
    ):
        with pytest.raises(DestinationNotFoundError):
            await service.create_message(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                bot_id=uuid.uuid4(),
                destination_ids=[uuid.uuid4()],
                content_type=MessageContentType.TEXT,
                text="hi",
                parse_mode=None,
                media_type=None,
                media_url=None,
                inline_keyboard=None,
                poll=None,
                template_id=None,
                template_variables=None,
                scheduled_at=None,
            )


@pytest.mark.asyncio
async def test_create_message_raises_when_destination_not_actively_subscribed() -> None:
    with (
        patch("app.modules.messaging.service.get_bot", AsyncMock(return_value=object())),
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=_OWNER_MEMBERSHIP),
        ),
        patch("app.modules.messaging.service.get_destination", AsyncMock(return_value=object())),
        patch(
            "app.modules.messaging.service.is_actively_subscribed", AsyncMock(return_value=False)
        ),
    ):
        with pytest.raises(DestinationNotSubscribedError):
            await service.create_message(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                bot_id=uuid.uuid4(),
                destination_ids=[uuid.uuid4()],
                content_type=MessageContentType.TEXT,
                text="hi",
                parse_mode=None,
                media_type=None,
                media_url=None,
                inline_keyboard=None,
                poll=None,
                template_id=None,
                template_variables=None,
                scheduled_at=None,
            )


@pytest.mark.asyncio
async def test_create_message_raises_when_scheduled_at_in_past() -> None:
    with (
        patch("app.modules.messaging.service.get_bot", AsyncMock(return_value=object())),
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=_OWNER_MEMBERSHIP),
        ),
        patch("app.modules.messaging.service.get_destination", AsyncMock(return_value=object())),
        patch("app.modules.messaging.service.is_actively_subscribed", AsyncMock(return_value=True)),
    ):
        with pytest.raises(ScheduledAtInPastError):
            await service.create_message(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                bot_id=uuid.uuid4(),
                destination_ids=[uuid.uuid4()],
                content_type=MessageContentType.TEXT,
                text="hi",
                parse_mode=None,
                media_type=None,
                media_url=None,
                inline_keyboard=None,
                poll=None,
                template_id=None,
                template_variables=None,
                scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
            )


@pytest.mark.asyncio
async def test_create_message_renders_template_and_falls_back_to_template_parse_mode() -> None:
    tenant_id = uuid.uuid4()
    template = _template(tenant_id=tenant_id, body="Hi {{name}}!", parse_mode=ParseMode.HTML)
    created_message = _message(tenant_id=tenant_id)

    with (
        patch("app.modules.messaging.service.get_bot", AsyncMock(return_value=object())),
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=_OWNER_MEMBERSHIP),
        ),
        patch("app.modules.messaging.service.get_destination", AsyncMock(return_value=object())),
        patch("app.modules.messaging.service.is_actively_subscribed", AsyncMock(return_value=True)),
        patch(
            "app.modules.messaging.service.get_message_template", AsyncMock(return_value=template)
        ),
        patch("app.modules.messaging.service.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.service.DeliveryLogRepository") as log_repo_cls,
    ):
        msg_repo_cls.return_value.create = AsyncMock(return_value=created_message)
        log_repo_cls.return_value.bulk_create_queued = AsyncMock(return_value=[])

        await service.create_message(
            AsyncMock(),
            tenant_id=tenant_id,
            actor_user_id=uuid.uuid4(),
            bot_id=uuid.uuid4(),
            destination_ids=[uuid.uuid4()],
            content_type=MessageContentType.TEXT,
            text=None,
            parse_mode=None,
            media_type=None,
            media_url=None,
            inline_keyboard=None,
            poll=None,
            template_id=template.id,
            template_variables={"name": "Alice"},
            scheduled_at=None,
        )

        create_kwargs = msg_repo_cls.return_value.create.call_args.kwargs
        assert create_kwargs["text"] == "Hi Alice!"
        assert create_kwargs["parse_mode"] == ParseMode.HTML


# --- enqueue_immediate_deliveries ---


def test_enqueue_immediate_deliveries_noop_when_scheduled() -> None:
    message = _message(scheduled_at=datetime.now(UTC) + timedelta(hours=1))
    with patch("app.modules.messaging.tasks.send_message_to_destination") as task:
        service.enqueue_immediate_deliveries(message, [_delivery_log()])
        task.delay.assert_not_called()


def test_enqueue_immediate_deliveries_dispatches_one_task_per_log() -> None:
    message = _message(scheduled_at=None)
    logs = [_delivery_log(), _delivery_log()]
    with patch("app.modules.messaging.tasks.send_message_to_destination") as task:
        service.enqueue_immediate_deliveries(message, logs)
        assert task.delay.call_count == 2
        task.delay.assert_any_call(delivery_log_id=str(logs[0].id))
        task.delay.assert_any_call(delivery_log_id=str(logs[1].id))


# --- get_message / get_message_status ---


@pytest.mark.asyncio
async def test_get_message_raises_when_not_found() -> None:
    with patch("app.modules.messaging.service.MessageRepository") as repo_cls:
        repo_cls.return_value.get_active = AsyncMock(return_value=None)

        with pytest.raises(MessageNotFoundError):
            await service.get_message(
                AsyncMock(),
                tenant_id=uuid.uuid4(),
                message_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
            )


@pytest.mark.asyncio
async def test_get_message_status_returns_message_and_logs() -> None:
    existing = _message()
    logs = [_delivery_log(message_id=existing.id)]
    with (
        patch("app.modules.messaging.service.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.service.DeliveryLogRepository") as log_repo_cls,
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=_OWNER_MEMBERSHIP),
        ),
    ):
        msg_repo_cls.return_value.get_active = AsyncMock(return_value=existing)
        log_repo_cls.return_value.list_for_message = AsyncMock(return_value=logs)

        message, result_logs = await service.get_message_status(
            AsyncMock(), tenant_id=uuid.uuid4(), message_id=existing.id, actor_user_id=uuid.uuid4()
        )

        assert message is existing
        assert result_logs == logs


# --- _assert_bot_access ("MEMBER can only access assigned bots") ---


@pytest.mark.asyncio
async def test_assert_bot_access_allows_owner_regardless_of_assignment() -> None:
    with (
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=Mock(role=OrganizationRole.OWNER)),
        ),
        patch(
            "app.modules.messaging.service.is_assigned", AsyncMock(return_value=False)
        ) as mock_is_assigned,
    ):
        await service._assert_bot_access(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )
        mock_is_assigned.assert_not_called()


@pytest.mark.asyncio
async def test_assert_bot_access_allows_admin_regardless_of_assignment() -> None:
    with (
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=Mock(role=OrganizationRole.ADMIN)),
        ),
        patch(
            "app.modules.messaging.service.is_assigned", AsyncMock(return_value=False)
        ) as mock_is_assigned,
    ):
        await service._assert_bot_access(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )
        mock_is_assigned.assert_not_called()


@pytest.mark.asyncio
async def test_assert_bot_access_allows_member_when_assigned() -> None:
    with (
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=Mock(role=OrganizationRole.MEMBER)),
        ),
        patch("app.modules.messaging.service.is_assigned", AsyncMock(return_value=True)),
    ):
        await service._assert_bot_access(
            AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_assert_bot_access_denies_member_when_not_assigned() -> None:
    with (
        patch(
            "app.modules.messaging.service.get_membership",
            AsyncMock(return_value=Mock(role=OrganizationRole.MEMBER)),
        ),
        patch("app.modules.messaging.service.is_assigned", AsyncMock(return_value=False)),
    ):
        with pytest.raises(BotNotAssignedError):
            await service._assert_bot_access(
                AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
            )


@pytest.mark.asyncio
async def test_assert_bot_access_denies_when_membership_missing_and_not_assigned() -> None:
    # Defensive path - should never happen since `require_permission`
    # already guarantees membership exists, but still default-denies if it does.
    with (
        patch("app.modules.messaging.service.get_membership", AsyncMock(return_value=None)),
        patch("app.modules.messaging.service.is_assigned", AsyncMock(return_value=False)),
    ):
        with pytest.raises(BotNotAssignedError):
            await service._assert_bot_access(
                AsyncMock(), tenant_id=uuid.uuid4(), bot_id=uuid.uuid4(), actor_user_id=uuid.uuid4()
            )


# --- _assert_bot_access via `X-Bot-Api-Key`
# (`actor_user_id=None`/`restrict_to_bot_id`) ---


@pytest.mark.asyncio
async def test_assert_bot_access_allows_bot_api_key_for_its_own_bot() -> None:
    bot_id = uuid.uuid4()
    with (
        patch("app.modules.messaging.service.get_membership") as mock_get_membership,
        patch("app.modules.messaging.service.is_assigned") as mock_is_assigned,
    ):
        await service._assert_bot_access(
            AsyncMock(),
            tenant_id=uuid.uuid4(),
            bot_id=bot_id,
            actor_user_id=None,
            restrict_to_bot_id=bot_id,
        )
        # There's no user/membership at all for an API key - must never
        # silently query membership/assignment.
        mock_get_membership.assert_not_called()
        mock_is_assigned.assert_not_called()


@pytest.mark.asyncio
async def test_assert_bot_access_denies_bot_api_key_for_a_different_bot() -> None:
    with pytest.raises(BotNotAssignedError):
        await service._assert_bot_access(
            AsyncMock(),
            tenant_id=uuid.uuid4(),
            bot_id=uuid.uuid4(),
            actor_user_id=None,
            restrict_to_bot_id=uuid.uuid4(),
        )


# --- dispatch_due_scheduled_messages ---


@pytest.mark.asyncio
async def test_dispatch_due_scheduled_messages_marks_dispatched_and_returns_logs() -> None:
    due_message = _message(
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1), dispatched_at=None
    )
    logs = [_delivery_log(message_id=due_message.id)]

    with (
        patch("app.modules.messaging.service.MessageRepository") as msg_repo_cls,
        patch("app.modules.messaging.service.DeliveryLogRepository") as log_repo_cls,
    ):
        msg_repo = msg_repo_cls.return_value
        msg_repo.list_due_for_dispatch = AsyncMock(return_value=[due_message])
        msg_repo.mark_dispatched = AsyncMock()
        log_repo_cls.return_value.list_for_message = AsyncMock(return_value=logs)

        result = await service.dispatch_due_scheduled_messages(AsyncMock())

        assert result == [(due_message, logs)]
        msg_repo.mark_dispatched.assert_awaited_once_with(due_message)


# --- cascade_delete_pending_messages ---


@pytest.mark.asyncio
async def test_cascade_delete_pending_messages_soft_deletes_only_pending() -> None:
    """Called by `modules/organizations.delete_organization` (the

    organization-delete cascade) - only `Message`s with `dispatched_at IS
    NULL` are cascaded; historical messages are NOT touched by this
    function (guaranteed by the `list_pending_for_tenant` mock, which the
    real repository already filters this way itself).
    """
    tenant_id = uuid.uuid4()
    pending_one = _message(dispatched_at=None)
    pending_two = _message(dispatched_at=None)

    with patch("app.modules.messaging.service.MessageRepository") as msg_repo_cls:
        msg_repo = msg_repo_cls.return_value
        msg_repo.list_pending_for_tenant = AsyncMock(return_value=[pending_one, pending_two])
        msg_repo.soft_delete = AsyncMock()

        await service.cascade_delete_pending_messages(AsyncMock(), tenant_id=tenant_id)

        msg_repo.list_pending_for_tenant.assert_awaited_once_with(tenant_id=tenant_id)
        assert msg_repo.soft_delete.await_count == 2


# --- purge_delivery_logs_batch ---


@pytest.mark.asyncio
async def test_purge_delivery_logs_batch_delegates_to_repository() -> None:
    tenant_id = uuid.uuid4()
    before = datetime.now(UTC)
    with patch("app.modules.messaging.service.DeliveryLogRepository") as repo_cls:
        repo_cls.return_value.delete_before = AsyncMock(return_value=7)

        result = await service.purge_delivery_logs_batch(
            AsyncMock(), tenant_id=tenant_id, before=before, batch_size=1000
        )

        assert result == 7
        repo_cls.return_value.delete_before.assert_awaited_once_with(
            tenant_id=tenant_id, before=before, batch_size=1000
        )
