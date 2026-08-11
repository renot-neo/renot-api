# API Conventions

Reference for anyone integrating against the renot-api HTTP API. See also
[API_VERSIONING.md](API_VERSIONING.md) for how the contract changes over
time — this document covers what the contract looks like today.

## Pagination

List endpoints accept `page` and `page_size` query parameters:

- `page`: 1-indexed, defaults to `1`.
- `page_size`: defaults to `20`, maximum `100`. Out-of-range `page`/`page_size`
  values (below `1`, or `page_size` above `100`) are rejected with a
  `422 Unprocessable` response by FastAPI's request validation, before the
  request reaches any handler code.

Paginated responses wrap their `data` as:

```json
{
  "items": [ /* ... */ ],
  "pagination": { "page": 1, "page_size": 20, "total": 42, "total_pages": 3 }
}
```

## Rate limits

All limits use a fixed 60-second window. Exceeding a limit returns
`429 Too Many Requests` with a `Retry-After` header (seconds until the
window resets).

| Group | Limit | Applies to | Keyed by |
| --- | --- | --- | --- |
| `auth` | 10 / 60s | `/api/v1/auth/*` | Client IP |
| `messaging_send` | 60 / 60s | `POST /api/v1/messages` | Tenant ID (JWT), or a hash of `X-Bot-Api-Key` when that's used instead, falling back to IP |
| `general` | 300 / 60s | Every other endpoint | User ID (JWT `sub`), falling back to IP |

## Error format

Every response — success or error — uses the same envelope:

```json
{
  "success": true,
  "data": { /* endpoint-specific payload, or null */ },
  "meta": { "request_id": "...", "timestamp": "..." },
  "error": null
}
```

On failure, `success` is `false`, `data` is `null`, and `error` is populated:

```json
{
  "success": false,
  "data": null,
  "meta": { "request_id": "...", "timestamp": "..." },
  "error": { "code": "VALIDATION_ERROR", "message": "...", "details": [] }
}
```

Error messages are localized based on the `Accept-Language` header —
`en` and `id` are currently supported.

## Authentication

Two supported auth methods, per-endpoint (see each router's own docstring
for exactly which one a given endpoint accepts):

- **JWT Bearer** — `Authorization: Bearer <token>`, used by dashboard/user
  requests.
- **`X-Bot-Api-Key` header** — used for external, non-dashboard integrations
  sending messages on behalf of a specific bot (e.g.
  `POST /api/v1/messages`).
