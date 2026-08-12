<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo/lockup-dark.png">
    <img alt="renot-api" src="docs/assets/logo/lockup-light.png" height="72">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/renot-neo/renot-api/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/renot-neo/renot-api/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/renot-neo/renot-api/releases"><img alt="Release" src="https://img.shields.io/github/v/release/renot-neo/renot-api"></a>
  <a href="pyproject.toml"><img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <a href="CONTRIBUTING.md"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg"></a>
</p>

<p align="center">
  <strong>Status:</strong> Pre-1.0 (<code>0.x</code>), following <a href="https://semver.org/">SemVer</a> — breaking changes may land in a minor release until <code>1.0.0</code>.
</p>

Managing a dozen Telegram bots across a dozen teams shouldn't mean a dozen different scripts. **renot-api** gives your whole organization one API to register bots, manage every subscriber — chats, groups, channels — and send or schedule messages, with delivery tracking, usage metering, and retention built in from day one.

## ✨ Features

- **Multi-tenant by design** — each organization's bots, destinations, and messages are fully isolated, with owner/admin/member roles (RBAC)
- **Register & manage Telegram bots** — validated live against the real Telegram API on registration, webhook auto-configured
- **Subscriber management** — personal chats, groups, group threads, and channels, with auto-subscribe the moment someone hits `/start`
- **Send & schedule messages** — text, media, or polls, right now or queued for later, with per-destination delivery tracking and automatic retry
- **Usage metering & retention** — every inbound/outbound event counted per plan, old data purged automatically per organization's retention window
- **Encrypted secrets at rest** — bot tokens and webhook secrets are Fernet-encrypted, never stored plaintext

## ⚡ Quick example

```bash
curl -X POST https://your-domain.example.com/api/v1/messages \
  -H "X-Bot-Api-Key: <bot-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "bot_id": "b3f1c2a0-1234-4a5b-8c9d-abcdef123456",
    "destination_ids": ["d1a2e4f6-1234-4a5b-8c9d-abcdef123456"],
    "content_type": "text",
    "text": "Deployment finished ✅"
  }'
```

```json
{
  "success": true,
  "data": {
    "id": "f4e2c1a0-1234-4a5b-8c9d-abcdef123456",
    "tenant_id": "a1b2c3d4-1234-4a5b-8c9d-abcdef123456",
    "bot_id": "b3f1c2a0-1234-4a5b-8c9d-abcdef123456",
    "template_id": null,
    "content_type": "text",
    "text": "Deployment finished ✅",
    "parse_mode": null,
    "media_type": null,
    "media_url": null,
    "inline_keyboard": null,
    "poll": null,
    "scheduled_at": null,
    "dispatched_at": null,
    "created_at": "2026-08-11T10:00:00+00:00"
  },
  "meta": { "request_id": "...", "timestamp": "..." },
  "error": null
}
```

See [docs/API_CONVENTIONS.md](docs/API_CONVENTIONS.md) for pagination, rate limits, the response envelope, and auth conventions — see the interactive docs (`/docs`, disabled in production) for full per-endpoint request/response schemas.

## 🧰 Tech stack

- **FastAPI** (async) + **Pydantic v2** for the HTTP API
- **PostgreSQL** via **SQLAlchemy 2.0** (async) + **Alembic** for migrations
- **Celery** + **Redis** for background work (message dispatch, webhook metering, retention purge) and rate limiting
- **structlog** for structured JSON logging

## 🚀 Getting started

### 1. Configure environment

```bash
cp .env.example .env.development
# edit .env.development with your local values
```

### 2. Start dependencies (Postgres, Redis, Celery worker/beat) via Docker

```bash
docker compose -f docker/docker-compose.yml up
```

This also starts the FastAPI app itself (`app` service) and `celery-flower` (a Celery monitoring UI at `localhost:5555`, dev-only). Postgres/Redis/Celery still need step 3 below to actually create the schema — nothing in `docker compose up` runs migrations for you.

To run the app directly on the host instead (e.g. for a debugger), install dependencies first, then continue from step 3:

```bash
pip install -r requirements-dev.txt
```

### 3. Run database migrations

```bash
alembic upgrade head
```

New migration:

```bash
alembic revision --autogenerate -m "description"
```

Then, if running the app on the host: `uvicorn app.main:app --reload`.

### 4. Explore the API

Once running locally, interactive API docs are available at
`http://localhost:8000/docs` (Swagger UI) and `http://localhost:8000/redoc`
(ReDoc). The browsable UIs are automatically disabled in production — the
underlying OpenAPI schema (`/api/v1/openapi.json`) is not, and stays
reachable in every environment by design. See
[docs/API_CONVENTIONS.md](docs/API_CONVENTIONS.md) for the API reference
that stays available regardless of environment.

## 📚 Documentation

- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, architecture, testing, linting, PR flow
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md) — vulnerability disclosure
- [SUPPORT.md](SUPPORT.md) — how to ask for help
- [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md) — what's stored, retention, self-hoster backup guidance
- [docs/API_VERSIONING.md](docs/API_VERSIONING.md) — backward-compatibility policy
- [docs/API_CONVENTIONS.md](docs/API_CONVENTIONS.md) — pagination, rate limits, error format, auth
- [LICENSE](LICENSE) — MIT
