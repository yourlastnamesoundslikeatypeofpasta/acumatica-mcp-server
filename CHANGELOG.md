# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.2.1] - 2026-07-10

Robustness and correctness fixes.

- Record keys are now URL-encoded per segment, so a key containing `#`, `?`, `%`, or
  spaces (e.g. a StockItem `#05773`) resolves correctly instead of being truncated by
  the URL parser.
- Connection and authentication failures now return the uniform `{ok: false}` error
  envelope instead of raising out of a tool call.
- The HTTP session is now thread-safe: login and lazy client creation are lock-guarded.
- `delete_record` returns the same key-format hint as `get_record` on a bad key.
- `rebuild_catalog.py` guards against infinite recursion on circular `$ref` schemas
  and drops a dead dedup branch.
- Removed a residual domain-specific docstring token and normalized remaining non-ASCII.

## [0.2.0] - 2026-07-10

Added a starter set of **skills**: repeatable, tested workflows layered on top of
the raw tools.

- New `skills/` folder with five read-only workflow skills: `recent-records`,
  `ap-health`, `ar-health`, `three-way-match`, and `doc-doctor`.
- README: new "Skills" section explaining the tools-vs-skills split and how to use
  them.

## [0.1.0] - 2026-07-10

Initial public release.

- 8 generic MCP tools covering the full Acumatica contract-based REST surface:
  `list_entities`, `describe_entity`, `list_records`, `get_record`,
  `upsert_record`, `delete_record`, `invoke_action`, `get_schema`.
- **Read-only by default**: mutating tools (`upsert_record`, `invoke_action`,
  `delete_record`) are disabled unless `ACUMATICA_ALLOW_WRITES=1` /
  `ACUMATICA_ALLOW_DELETES=1` are set, so the server is safe to point at a
  production tenant for exploration.
- Cookie-based authentication with automatic re-login on session expiry (401).
- Browser deep-link (`browser_url`) generation for common entities, so records
  returned by the tools link straight to the Acumatica web UI.
- Bundled `entity_catalog.json` (standard Acumatica entities) plus
  `rebuild_catalog.py` to regenerate the catalog from your own tenant's
  OpenAPI spec.
- Multi-tenant support via the `ACUMATICA_ENV_FILE` environment variable.
