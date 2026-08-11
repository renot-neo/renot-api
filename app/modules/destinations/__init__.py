"""Module `destinations` - the service interface exposed to other modules.

Scope: message destinations (chats/groups/channels) and their bot subscriptions.

Cross-module communication MUST go through the interface exposed here (or an
event/message for async/eventual things) - other modules MUST NOT
`from app.modules.destinations.model import X` directly.

`subscribe_via_start`/`unsubscribe_via_stop` are exposed for `modules/webhooks`,
which processes inbound `/start`/`/stop` commands from Telegram.
`is_actively_subscribed` is exposed for `modules/messaging`, which needs to
validate that each message's destination is actively subscribed to the
relevant bot.
"""

from __future__ import annotations

from app.modules.destinations.model import (
    BotDestinationSubscription,
    Destination,
    DestinationType,
    SubscriptionStatus,
)
from app.modules.destinations.service import (
    get_destination,
    is_actively_subscribed,
    list_destinations_for_bot,
    subscribe_via_start,
    unsubscribe_via_stop,
)

__all__ = [
    "get_destination",
    "is_actively_subscribed",
    "list_destinations_for_bot",
    "subscribe_via_start",
    "unsubscribe_via_stop",
    "BotDestinationSubscription",
    "Destination",
    "DestinationType",
    "SubscriptionStatus",
]
