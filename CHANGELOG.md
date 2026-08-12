# Changelog

## [0.5.0](https://github.com/renot-neo/renot-api/compare/v0.4.1...v0.5.0) (2026-08-12)


### Features

* **destinations:** add get_subscription_status read-only lookup ([26e6260](https://github.com/renot-neo/renot-api/commit/26e6260e0be15d8eb4767b807e807874d39c84d3))
* **webhooks:** add /about command ([2a70d39](https://github.com/renot-neo/renot-api/commit/2a70d39078bd1ef5e3b9319b04ed572c83978e8b))
* **webhooks:** humanize /start and /stop replies, switch to parse_mode=HTML ([92a3860](https://github.com/renot-neo/renot-api/commit/92a3860dc84b05329db74be5ada56ecb1a8ff3c0))
* **webhooks:** humanize /status with subscription state + tap-to-copy IDs ([aa0cfd6](https://github.com/renot-neo/renot-api/commit/aa0cfd636126f1579751fba8c81597530e1e1307))
* **webhooks:** list all commands inline in /help reply ([cd3cf17](https://github.com/renot-neo/renot-api/commit/cd3cf173e1f4d2a9f41776103ff9a5cbda22e6a9))


### Bug Fixes

* resolve final-review findings (ruff E501, stale command-list docs) ([84bb2d6](https://github.com/renot-neo/renot-api/commit/84bb2d612916858a982fa7b7b28b00e51dc1ca81))


### Documentation

* document soft-delete-docstring convention in CONTRIBUTING.md ([9ea2ed7](https://github.com/renot-neo/renot-api/commit/9ea2ed77773a768646c7caeedbb860071bb1a3f2))
* trim README to an adopter-focused pitch, move architecture to CONTRIBUTING.md ([f87674f](https://github.com/renot-neo/renot-api/commit/f87674f1f2b36d93b448969b03ece1942527b56a))

## [0.4.1](https://github.com/renot-neo/renot-api/compare/v0.4.0...v0.4.1) (2026-08-11)


### Bug Fixes

* :latest GHCR tag now only moves on a release, not every main push ([f0abfbc](https://github.com/renot-neo/renot-api/commit/f0abfbc527d59b4208b0b8abc8e9e1a7ef1ef839))

## [0.4.0](https://github.com/renot-neo/renot-api/compare/v0.3.0...v0.4.0) (2026-08-11)


### Features

* encrypt Bot.token and Bot.webhook_secret at rest ([55d62b1](https://github.com/renot-neo/renot-api/commit/55d62b1a70f8917c1088731310108cf3f87c6da7))


### Documentation

* update DATA_HANDLING.md - Bot.token/webhook_secret now encrypted ([a2ae280](https://github.com/renot-neo/renot-api/commit/a2ae280ad08fa5deb0fa471903c70595e14e9a7e))

## [0.3.0](https://github.com/renot-neo/renot-api/compare/v0.2.1...v0.3.0) (2026-08-11)


### Features

* add project logo, wire branded favicon, build social preview image ([4a8208e](https://github.com/renot-neo/renot-api/commit/4a8208e5b28fccfa31097c75e64eeb836b04821e))

## [0.2.1](https://github.com/renot-neo/renot-api/compare/v0.2.0...v0.2.1) (2026-08-11)


### Bug Fixes

* exempt release-please's own release PR from the DCO check ([7852dc8](https://github.com/renot-neo/renot-api/commit/7852dc8ce3a562c4bea897f0201d33977a9c09a2))

## [0.2.0](https://github.com/renot-neo/renot-api/compare/v0.1.0...v0.2.0) (2026-08-11)


### Features

* disable interactive docs in production, hide webhook route from schema ([1840b51](https://github.com/renot-neo/renot-api/commit/1840b5172ac63993b1495e61a1e0312d0e4fa885))


### Bug Fixes

* correct CD attestation driver, release-please config mode, and DCO edge cases from final review ([76d0b5f](https://github.com/renot-neo/renot-api/commit/76d0b5f12aec82cf34d41482bc86e215bcc39ebf))
* correct README API example, doc gaps, and tool-version drift from final review ([3c03df0](https://github.com/renot-neo/renot-api/commit/3c03df0fde0466658d3f22536d724c663a112ec0))
* mock Celery .delay() in purge integration test to avoid real broker connection ([8a48cc1](https://github.com/renot-neo/renot-api/commit/8a48cc1b9db58761647c2d25ac1bc587630dfb1a))
* read app version from pyproject.toml instead of a hardcoded string ([f19e066](https://github.com/renot-neo/renot-api/commit/f19e066e2319331bf73df41a5e3974a02c8fa493))


### Documentation

* add API_CONVENTIONS.md, cross-link with API_VERSIONING.md ([3a40b42](https://github.com/renot-neo/renot-api/commit/3a40b422517e0d7b3ba2c7d94668ca1273dc1db0))
* add API_VERSIONING.md backward-compatibility policy ([21a0f24](https://github.com/renot-neo/renot-api/commit/21a0f24c203149ee78dda833cb6711be028cf0f0))
* add CONTRIBUTING.md with dev setup, testing tiers, PR flow ([7b1e08d](https://github.com/renot-neo/renot-api/commit/7b1e08d1eee6b149a36f03ec3c59c96943f0578e))
* add Contributor Covenant v2.1 code of conduct ([3acc4f0](https://github.com/renot-neo/renot-api/commit/3acc4f0e10bc21a0334f6d42f9d2a81b4362a7ea))
* add DATA_HANDLING.md for self-hosters ([02657d9](https://github.com/renot-neo/renot-api/commit/02657d9c2083473b4b8faae94403bbce5bbc947c))
* add MIT LICENSE ([b249eaf](https://github.com/renot-neo/renot-api/commit/b249eaf414f8d32fec8eab6eebea3aba9d082f61))
* add SECURITY.md vulnerability disclosure policy ([8da389e](https://github.com/renot-neo/renot-api/commit/8da389e96554074631b5c54e11399560d1f77be0))
* add SUPPORT.md ([281b1e3](https://github.com/renot-neo/renot-api/commit/281b1e3fb95ab85d674d80f9df9d4dfe8d6aef9a))
* fix inaccurate pagination clamping claim in API_CONVENTIONS.md ([548bb55](https://github.com/renot-neo/renot-api/commit/548bb55dd9931a20ef6720d8c28c92cf0b51eaa5))
* link governance/docs files, add architecture diagram and quick-start example to README ([d3d79f2](https://github.com/renot-neo/renot-api/commit/d3d79f2fe237f2b2055113289d444bb4087179ae))
* replace &lt;your-org&gt; placeholder with real repo path (renot-neo/renot-api) ([50e4b8c](https://github.com/renot-neo/renot-api/commit/50e4b8c67adff34364fe3095ce5598374b2f9151))
