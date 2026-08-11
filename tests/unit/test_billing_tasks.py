"""Unit tests for `app.modules.billing.tasks`.

Pure logic - `WorkerAsyncSessionFactory`/`service.create_usage_event` are
mocked, and the Celery task is called directly (not via `.delay()`) - same
pattern as `tests/unit/test_messaging_tasks.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from celery.exceptions import Retry

from app.modules.billing import tasks
from app.modules.billing.model import UsageEventType


class _FakeAsyncSessionCM:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _session_factory(session: object) -> object:
    return lambda: _FakeAsyncSessionCM(session)


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_record_usage_event_async_writes_event_in() -> None:
    session = _mock_session()
    tenant_id = uuid.uuid4()
    bot_id = uuid.uuid4()
    with (
        patch("app.modules.billing.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.billing.tasks.service.create_usage_event", AsyncMock()) as create,
    ):
        await tasks._record_usage_event_async(
            tenant_id=str(tenant_id),
            event_type="in",
            bot_id=str(bot_id),
            destination_id=None,
            message_id=None,
            delivery_log_id=None,
        )

        create.assert_awaited_once_with(
            session,
            tenant_id=tenant_id,
            event_type=UsageEventType.IN,
            bot_id=bot_id,
            destination_id=None,
            message_id=None,
            delivery_log_id=None,
        )
        session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_usage_event_async_writes_event_out_with_all_ids() -> None:
    session = _mock_session()
    ids = {
        name: uuid.uuid4()
        for name in ("tenant_id", "bot_id", "destination_id", "message_id", "delivery_log_id")
    }
    with (
        patch("app.modules.billing.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.billing.tasks.service.create_usage_event", AsyncMock()) as create,
    ):
        await tasks._record_usage_event_async(
            tenant_id=str(ids["tenant_id"]),
            event_type="out",
            bot_id=str(ids["bot_id"]),
            destination_id=str(ids["destination_id"]),
            message_id=str(ids["message_id"]),
            delivery_log_id=str(ids["delivery_log_id"]),
        )

        create.assert_awaited_once_with(
            session,
            tenant_id=ids["tenant_id"],
            event_type=UsageEventType.OUT,
            bot_id=ids["bot_id"],
            destination_id=ids["destination_id"],
            message_id=ids["message_id"],
            delivery_log_id=ids["delivery_log_id"],
        )


def test_record_usage_event_retries_on_transient_failure() -> None:
    """Same pattern as `test_messaging_tasks.py` regarding `self.retry()` +

    the `called_directly` quirk - see the docstring there.
    """
    exc = RuntimeError("db hiccup")
    with (
        patch("app.modules.billing.tasks._record_usage_event_async", AsyncMock(side_effect=exc)),
        patch.object(tasks.record_usage_event, "retry", side_effect=Retry("mocked")) as retry,
    ):
        with pytest.raises(Retry):
            tasks.record_usage_event(
                tenant_id=str(uuid.uuid4()), event_type="in", bot_id=str(uuid.uuid4())
            )

        assert retry.call_args.kwargs["exc"] is exc
        assert retry.call_args.kwargs["countdown"] == 1  # 2**0 (self.request.retries == 0)


def test_record_usage_event_logs_and_gives_up_after_max_retries() -> None:
    original_max_retries = tasks.record_usage_event.max_retries
    tasks.record_usage_event.max_retries = 0
    try:
        with (
            patch(
                "app.modules.billing.tasks._record_usage_event_async",
                AsyncMock(side_effect=RuntimeError("db hiccup")),
            ),
            patch("app.modules.billing.tasks.logger") as logger,
        ):
            # Tidak boleh raise - kegagalan permanen metering di-log, bukan
            # di-propagate (§8 "boleh eventual", lihat docstring module).
            tasks.record_usage_event(
                tenant_id=str(uuid.uuid4()), event_type="in", bot_id=str(uuid.uuid4())
            )

            logger.error.assert_called_once()
    finally:
        tasks.record_usage_event.max_retries = original_max_retries


# --- purge_expired_usage_data (retention) ---


@pytest.mark.asyncio
async def test_purge_all_batches_loops_until_empty_and_commits_between_batches() -> None:
    session = _mock_session()
    batch_fn = AsyncMock(side_effect=[3, 3, 1, 0])

    total = await tasks._purge_all_batches(batch_fn, session)

    assert total == 7
    assert batch_fn.await_count == 4
    # Commit after each non-empty batch, NOT after the final `0` (nothing
    # new to persist at that point).
    assert session.commit.await_count == 3


@pytest.mark.asyncio
async def test_purge_all_batches_no_commit_when_first_batch_already_empty() -> None:
    session = _mock_session()
    batch_fn = AsyncMock(return_value=0)

    total = await tasks._purge_all_batches(batch_fn, session)

    assert total == 0
    batch_fn.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_expired_usage_data_async_sums_per_tenant_and_uses_retention_cutoff() -> None:
    session = _mock_session()
    tenant_id = uuid.uuid4()

    with (
        patch("app.modules.billing.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch(
            "app.modules.billing.tasks.service.get_purge_targets",
            AsyncMock(return_value=[(tenant_id, 30)]),
        ),
        patch(
            "app.modules.billing.tasks.service.purge_usage_events_batch",
            AsyncMock(side_effect=[5, 0]),
        ) as usage_batch,
        patch(
            "app.modules.billing.tasks.purge_delivery_logs_batch",
            AsyncMock(side_effect=[2, 0]),
        ) as delivery_batch,
    ):
        summary = await tasks._purge_expired_usage_data_async()

    assert summary == {
        "organizations_processed": 1,
        "usage_events_deleted": 5,
        "delivery_logs_deleted": 2,
    }
    assert usage_batch.await_count == 2
    assert delivery_batch.await_count == 2

    first_call_kwargs = usage_batch.await_args_list[0].kwargs
    assert first_call_kwargs["tenant_id"] == tenant_id
    expected_cutoff = datetime.now(UTC) - timedelta(days=30)
    assert abs((expected_cutoff - first_call_kwargs["before"]).total_seconds()) < 5


@pytest.mark.asyncio
async def test_purge_expired_usage_data_async_no_targets_returns_zero_summary() -> None:
    session = _mock_session()
    with (
        patch("app.modules.billing.tasks.WorkerAsyncSessionFactory", _session_factory(session)),
        patch("app.modules.billing.tasks.service.get_purge_targets", AsyncMock(return_value=[])),
    ):
        summary = await tasks._purge_expired_usage_data_async()

    assert summary == {
        "organizations_processed": 0,
        "usage_events_deleted": 0,
        "delivery_logs_deleted": 0,
    }


def test_purge_expired_usage_data_logs_summary() -> None:
    summary = {"organizations_processed": 1, "usage_events_deleted": 5, "delivery_logs_deleted": 2}
    with (
        patch(
            "app.modules.billing.tasks._purge_expired_usage_data_async",
            AsyncMock(return_value=summary),
        ),
        patch("app.modules.billing.tasks.logger") as logger,
    ):
        tasks.purge_expired_usage_data()

        logger.info.assert_called_once_with("usage_data_purged", **summary)
