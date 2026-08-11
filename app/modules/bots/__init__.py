"""Module `bots` - the service interface exposed to other modules.

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.

Cross-module communication MUST go through the interface exposed here -
other modules MUST NOT `from app.modules.bots.model import Bot` directly.

`get_bot_token` is exposed for `modules/messaging`, which needs to call the
Telegram API on this bot's behalf. `get_bot_for_webhook` is exposed for
`modules/webhooks` - a tenant-agnostic lookup specifically for inbound
webhooks (see its docstring in `service.py`). `is_assigned` is exposed for
`modules/messaging` - scoping the MEMBER role's `message:send`/`log:view` to
assigned bots only. `cascade_delete_for_organization` is exposed for
`modules/organizations` (the cascade run by `DELETE /organizations/{id}` -
see its docstring in `service.py`). `get_bot_by_api_key` is exposed for
`core.deps.get_bot_from_api_key` - dual-auth for the external message-send
endpoint, via the `X-Bot-Api-Key` header.
"""

from __future__ import annotations

from app.modules.bots.model import Bot
from app.modules.bots.service import (
    cascade_delete_for_organization,
    get_bot,
    get_bot_by_api_key,
    get_bot_for_webhook,
    get_bot_token,
    is_assigned,
)

__all__ = [
    "cascade_delete_for_organization",
    "get_bot",
    "get_bot_by_api_key",
    "get_bot_for_webhook",
    "get_bot_token",
    "is_assigned",
    "Bot",
]
