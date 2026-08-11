"""Module `bots` - the service interface exposed to other modules.

Scope: bot onboarding (register a Telegram bot) and bot CRUD/assignment.

Cross-module communication MUST go through the interface exposed here -
other modules MUST NOT `from app.modules.bots.model import Bot` directly.

`get_bot_token`/`get_bot_webhook_secret` are exposed for callers that only
have `(tenant_id, bot_id)`, not an already-loaded `Bot`. `reveal_token`/
`reveal_webhook_secret` are exposed for callers that already hold a `Bot`
object (from `get_bot_for_webhook` etc.) and just need its decrypted
secrets without a redundant DB round-trip - both `modules/messaging` (to
call the Telegram API, and to HMAC-sign outbound callbacks) and
`modules/webhooks` (to validate the inbound secret token, and to reply via
the Telegram API) use these. `get_bot_for_webhook` is exposed for
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
    get_bot_webhook_secret,
    is_assigned,
    reveal_token,
    reveal_webhook_secret,
)

__all__ = [
    "cascade_delete_for_organization",
    "get_bot",
    "get_bot_by_api_key",
    "get_bot_for_webhook",
    "get_bot_token",
    "get_bot_webhook_secret",
    "is_assigned",
    "reveal_token",
    "reveal_webhook_secret",
    "Bot",
]
