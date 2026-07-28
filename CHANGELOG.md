# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Read-only automated Kraken futures-paper monitor for service health, account state, positions, exact protective stops, fills, journal cycles, audit-ledger integrity, and experiment provenance.
- Backend and frontend regression suites, including strict-render, active-WAL, file-race, stale-input, malformed-number/schema/path, provenance-correlation, retained-cache, and cross-instrument protection probes.
- Pinned GitHub Actions CI for backend tests, frontend syntax, and the frontend state matrix.
- Inert `/balance` and `/trades` compatibility routes that never call authenticated Kraken commands.

### Changed

- `/summary` now preserves its legacy `balance`/`positions` envelope and exposes the complete monitor under `paper_dashboard`.
- `/positions` now reports normalized futures-paper positions and dashboard-health errors.
- Paper-state, ticker, service, and ledger readers now fail closed on malformed, stale, non-finite, untrusted, or inconsistent inputs; provenance validation rejects malformed, untrusted, or contract-inconsistent seals without imposing the cycle-freshness window on experiment age.
- The main panel and statusbar reject failed refetches and cached paper responses older than 30 seconds instead of retaining healthy output.

### Security

- Removed authenticated balance and trade-history polling from the backend.
- Restricted subprocesses to descriptor-pinned absolute executables whose files and parent directories pass owner, mode, link, and symlink checks, with validated arguments, a minimal environment, and redacted failure output.
- Switched ledger inspection to an immutable SQLite file descriptor that rejects active WAL data, hard links, path-replacement races, and non-canonical event schemas without creating sidecars.
- Parsed JSON decimals losslessly, rejected duplicate object keys and non-injective conversions, and retained exact source arithmetic for protective-stop, sealed-baseline, and exposure enforcement.
- Enforced shared futures-symbol and protective client-order-ID grammars across the backend and frontend, and contained malformed service-environment paths before filesystem access.
- Pinned every kill-switch ancestor through descriptor-relative no-symlink traversal and required canonical lowercase provenance and ledger digests across backend and frontend validation.
- Bound sealed provenance to one lexically canonical release/runtime ledger path free of Unicode control, format, surrogate, private-use, or unassigned characters; required the automation release to equal the experiment commit; adopted a shared conservative ASCII grammar for machine identities; required complete, unambiguous service-environment parsing and ASCII journal microseconds; contained UTC timestamp-normalisation overflow; and aligned backend/frontend timestamp, fill, provenance, risk-limit, and exact nanosecond freshness and chronology validation without treating unrelated safe JSON records as malformed cycles.

### Upgrade notes

Install `plugin.js` and `backend/dashboard/plugin_api.py` from the same commit, then restart Hermes. Consumers should migrate to `GET /paper-dashboard`; the legacy account routes intentionally return retirement errors.
