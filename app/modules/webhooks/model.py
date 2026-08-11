"""SQLAlchemy models for the `webhooks` module.

Scope: processing inbound Telegram webhooks.

This module deliberately has no entity/table of its own for the MVP - it's
purely a receiver that reads `Bot` (via
`app.modules.bots.get_bot_for_webhook`) and mutates `Destination`/
`BotDestinationSubscription` (via
`app.modules.destinations.subscribe_via_start`/`unsubscribe_via_stop`) -
both of those service interfaces already handle tenant-scoping &
soft-delete in their own modules, so there's no new state to store here.

The `/start`/`/stop` commands are naturally idempotent (subscribe/
unsubscribe is a "set status" operation, not "insert a new row every time"
- see `modules/destinations/service.py`), so a separate
`update_id`/idempotency-key dedup table isn't needed for this MVP.

If a future need arises for a raw inbound-webhook audit log (e.g. for
debugging Telegram payloads), that would be a new entity with its own
justification - not assumed or built here.
"""

from __future__ import annotations
