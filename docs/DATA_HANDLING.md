# Data Handling

This documents what a self-hosted renot-api instance stores, how sensitive
it is, and what gets cleaned up automatically vs. kept indefinitely. It's
technical documentation for self-hosters — **not** a legal Privacy Policy or
Terms of Service. If renot-api is ever operated as a hosted commercial
service, a Privacy Policy/ToS would be a separate, business-owned document.

## What's stored, and how

| Data | Storage | Retention |
| --- | --- | --- |
| `User.password_hash` | Hashed | No auto-purge (active account data) |
| `Bot.api_key_hash` | Hashed | No auto-purge (active resource) |
| `Bot.token` (the real Telegram bot token) | 🟢 Encrypted at rest (Fernet, `TELEGRAM__TOKEN_ENCRYPTION_KEY`) | Kept as long as the bot exists |
| `Bot.webhook_secret` (validated against the `X-Telegram-Bot-Api-Secret-Token` header on inbound webhooks) | 🟢 Encrypted at rest (Fernet, `TELEGRAM__TOKEN_ENCRYPTION_KEY`) | Kept as long as the bot exists |
| `RefreshToken.token_hash` | Hashed | Kept until revoked/expired (not auto-purged) |
| `User.email` / `User.full_name` | Plaintext PII | No auto-purge (active account data) |
| `Destination.chat_id` / `Destination.title` (Telegram chat/group/channel identifiers) | Plaintext | No auto-purge |
| `Message` / `MessageTemplate` content (actual message text/media/poll data) | Plaintext, soft-delete only | 🔴 Not covered by the automated retention purge below — content persists indefinitely (soft-deleted, recoverable) |
| `DeliveryLog`, `UsageEvent` | Plaintext | Auto-purged per organization's `Plan.retention_days` (daily job, see below) |

**`Bot.token` and `Bot.webhook_secret` are encrypted at rest** (Fernet -
authenticated symmetric encryption, via the `cryptography` package), keyed
by a single static key from the `TELEGRAM__TOKEN_ENCRYPTION_KEY` env var.
Self-hosters: this key is the one secret that, if lost, makes every stored
bot token/webhook secret unrecoverable (nothing else can decrypt them) -
back it up like any other production secret, separately from your database
backups. If it's ever compromised, treat it the same as a full plaintext
leak of every `Bot.token`/`webhook_secret` and rotate every affected bot's
Telegram token via `@BotFather`.

## Automated retention purge

A daily Celery beat job (`purge_expired_usage_data`) hard-deletes
`DeliveryLog` and `UsageEvent` rows older than each organization's
`Plan.retention_days` (nullable — `None` means "keep forever," and
organizations on a plan with no retention limit are never purged). This is
the *only* automated data deletion in the system — it does not touch
`Message`/`MessageTemplate` content, `Bot`, `Destination`, or `User` rows.

## Mitigation recommendations for self-hosters

- **Restrict database access** to the application and trusted operators
  only. `Bot.token`/`webhook_secret` are encrypted, so DB access alone
  isn't equivalent to bot credential access anymore — but treat it as
  equivalent anyway if the same operators/hosts also have access to
  `TELEGRAM__TOKEN_ENCRYPTION_KEY` (commonly true, e.g. the same `.env`
  file or secrets manager).
- **Keep `TELEGRAM__TOKEN_ENCRYPTION_KEY` separate from your database
  backups** — a DB dump alone is not enough to recover bot tokens without
  it, which is the point of encrypting them; don't undo that by storing
  both together.
- **Encrypt disks at rest** for whatever host runs Postgres.
- **Rotate a bot's Telegram token** (via `@BotFather`) if you ever suspect
  the database AND the encryption key have both been compromised — treat
  that combination as a full token leak. Rotate each bot's
  `webhook_secret` too (re-register the webhook).
- **Restrict Redis access** the same way — it holds Celery broker/result
  state and rate-limit counters; not a durable store, but still
  operationally sensitive while a request is in flight.

## Backup & recovery

Postgres is the only durable store that needs backing up. Redis is
cache/broker/rate-limit state — ephemeral by design, nothing here to
back up.

Minimal example for the common Docker Compose setup:

```bash
docker compose -f docker/docker-compose.yml exec postgres \
  pg_dump -U renot renot_dev > renot-backup-$(date +%Y%m%d).sql
```

Restore:

```bash
docker compose -f docker/docker-compose.yml exec -T postgres \
  psql -U renot renot_dev < renot-backup-YYYYMMDD.sql
```

**Caveat:** a backup taken *before* the retention purge job runs can still
contain `DeliveryLog`/`UsageEvent` rows that have since "expired" per an
organization's `Plan.retention_days`. Restoring an old backup can
reintroduce data that was deliberately purged — worth knowing before relying
on backups as your retention-compliance mechanism.
