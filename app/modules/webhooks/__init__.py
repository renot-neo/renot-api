"""Module `webhooks` - the service interface exposed to other modules.

Scope: processing inbound Telegram webhooks.

This module is a "leaf" - no other module needs to call back into
`webhooks` (unlike `bots`/`destinations`, which expose interfaces for other
modules to use), so nothing is exposed here for now.
"""

from __future__ import annotations
