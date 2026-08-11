# API Backward-Compatibility Policy

This covers the **HTTP API contract** (`/api/v1/...`) — a distinct thing
from *software* versioning (git tags, `pyproject.toml`'s `version` field).
See [README.md](../README.md) for the software-release versioning scheme.

## Current status

`/api/v1` is the only API version that exists today. There is currently no
`Deprecation`/`Sunset` header handling or version-negotiation logic in the
codebase — this document describes the *policy* the maintainer follows,
not an enforcement mechanism that exists yet.

## Breaking-change strategy

Breaking changes are shipped as a **parallel `/api/v2`**, never as in-place
breaking changes to `/api/v1`. Both versions run side by side during a
deprecation window; `/api/v1` keeps working the whole time. The window is
communicated via:

- `Deprecation` and `Sunset` HTTP response headers on the deprecated
  version's responses (once `/api/v2` exists — not implemented today, since
  there is no `/api/v2` yet).
- A `CHANGELOG.md` entry once release/version tooling is in place.

## What counts as breaking vs. non-breaking

**Breaking** (requires a new API version):
- Removing or renaming an endpoint or a response/request field
- Changing a field's type or meaning
- Tightening validation on previously-accepted input
- Changing authentication requirements for an endpoint

**Non-breaking** (safe within `/api/v1`):
- New endpoints
- New optional request fields
- New response fields (additive)
- Relaxing validation on previously-rejected input

## When this policy becomes strict

**Loose until the first real external customer integrates against the API,
strict after.** Pre-customer, `/api/v1` may still evolve more freely while
the product is finding fit — formalizing a strict contract before anyone
depends on it would slow iteration for no real benefit to anyone. Once a
real customer has a live integration, the breaking/non-breaking distinction
above is enforced going forward.

## Deprecation window length

Not committed to a specific number of days/months yet — deliberately left
open until there's a real customer situation to calibrate against, rather
than picking an arbitrary number with no basis.
