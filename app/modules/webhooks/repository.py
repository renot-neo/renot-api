"""Data access for the `webhooks` module.

Scope: processing inbound Telegram webhooks.

There's no repository here - this module has no model/table of its own
(see the rationale in `model.py`). The data access the webhook handler
needs (find a `Bot`, get-or-create a `Destination`, change a
`BotDestinationSubscription`'s status) all goes through the public service
interfaces `app.modules.bots` and `app.modules.destinations`, called
directly from `modules/webhooks/service.py`.
"""

from __future__ import annotations
