# Security Policy

## Supported Versions

renot-api is currently pre-1.0 (`0.x`, see [README.md](README.md) for the
maturity statement). Only the latest released version is supported with
security fixes — there is no long-term-support branch at this stage.

| Version | Supported |
| ------- | --------- |
| latest `0.x` | ✅ |
| anything older | ❌ |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report suspected vulnerabilities privately by emailing
**sandimvlyadi@gmail.com** with:

- A description of the vulnerability and its potential impact
- Steps to reproduce it (a minimal repro is very helpful)
- Any suggested fix, if you have one

### What to expect

This is a single-maintainer project worked on outside a main job — reports
are handled **best-effort, with no guaranteed response-time SLA**. You can
expect an acknowledgment as soon as the maintainer is able to review it, and
follow-up once the report has been triaged. This is the same honest,
no-fixed-SLA stance documented in [SUPPORT.md](SUPPORT.md) for
non-security issues.

## Data handled by this project

If your report relates to what data a self-hosted instance stores and how
sensitive it is (e.g. database compromise scenarios), see
[docs/DATA_HANDLING.md](docs/DATA_HANDLING.md) for what's stored in
plaintext, hashed, or encrypted, and current mitigation recommendations.
