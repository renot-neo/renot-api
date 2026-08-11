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
| `Bot.token` (the real Telegram bot token) | 🔴 **Plaintext** — no encryption module exists in `app/` today | Kept as long as the bot exists |
| `Destination.chat_id` / `Destination.title` (Telegram chat/group/channel identifiers) | Plaintext | No auto-purge |
| `Message` / `MessageTemplate` content (actual message text/media/poll data) | Plaintext, soft-delete only | 🔴 Not covered by the automated retention purge below — content persists indefinitely (soft-deleted, recoverable) |
| `DeliveryLog`, `UsageEvent` | Plaintext | Auto-purged per organization's `Plan.retention_days` (daily job, see below) |

**`Bot.token` is stored in plaintext.** This is a known, currently accepted
gap — encrypting it at rest is tracked as a future security improvement, not
implemented today. Stated here plainly so self-hosters can make an informed
decision, not glossed over.

## Automated retention purge

A daily Celery beat job (`purge_expired_usage_data`) hard-deletes
`DeliveryLog` and `UsageEvent` rows older than each organization's
`Plan.retention_days` (nullable — `None` means "keep forever," and
organizations on a plan with no retention limit are never purged). This is
the *only* automated data deletion in the system — it does not touch
`Message`/`MessageTemplate` content, `Bot`, `Destination`, or `User` rows.

## Mitigation recommendations for self-hosters

- **Restrict database access** to the application and trusted operators
  only — given `Bot.token` is plaintext, DB access is equivalent to bot
  credential access.
- **Encrypt disks at rest** for whatever host runs Postgres.
- **Rotate a bot's Telegram token** (via `@BotFather`) if you ever suspect
  the database has been compromised — treat any DB exposure as a token
  leak.
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
