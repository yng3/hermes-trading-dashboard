# Hermes Trading Dashboard

A native [Hermes Agent](https://github.com/NousResearch/hermes-agent) desktop plugin for watching public crypto markets and a local automated Kraken futures-paper experiment.

## What it shows

- **Automation health** — timer, execution mode, kill switch, leverage policy, and ledger integrity
- **Paper account** — starting collateral, equity, net P&L, exposure, and fees
- **Current position** — side, size, entry, mark, notional, leverage, and unrealized P&L
- **Protective stop** — trigger price, trigger signal, reduce-only status, size, and order ID
- **Recent fills** — timestamp, side, size, price, fee, and order ID
- **Recent bot cycles** — structured decisions from the systemd journal
- **Audit ledger** — event count, hash-chain status, head hash, and recent events
- **Experiment provenance** — experiment ID, seal-file SHA-256, release commit/tree, limits, and start time
- **Statusbar monitor** — paper health, PF_ETHUSD mark, and net P&L at a glance
- **Public market cards** — CoinGecko asset cards, refreshed every 60 seconds

The interface is deliberately **read-only**. It contains no order-entry, start/stop, or kill-switch controls.

## Data-safety model

The 15-second dashboard refresh does not run a Kraken paper-state command. Some paper simulators reconcile stops as a side effect of a nominally read-only CLI query, which would alter experiment timing. Instead, this backend:

1. opens the local futures-paper state and experiment metadata with bounded, no-symlink, owner/mode-checked reads;
2. reads the paper ledger through an immutable SQLite file descriptor, without creating WAL/SHM sidecars;
3. reads service/timer state through `systemctl --user show`;
4. reads recent cycles through `journalctl`; and
5. fetches only the public PF_ETHUSD futures ticker for the current mark.

Subprocesses run through inherited file descriptors pinned after the executable and its parent hierarchy pass owner, mode, link, and no-symlink checks. They receive a minimal environment, so unrelated Hermes provider/API variables are not inherited, and malformed arguments or service-provided paths are contained as unhealthy results. Service `Environment=` serialization must parse completely and unambiguously; partial recovery from unmatched quoting, bare tokens, duplicate names, or invalid names fails aggregate health. Kill-switch absence is trusted only after a descriptor-relative walk from the filesystem root pins every ancestor and rejects symlinks, untrusted owners, or writable directories. Malformed, non-finite, numerically unsafe, mismatched, or untrusted state, ticker, ledger, provenance, journal, or service data fails the dashboard health check instead of being converted into healthy-looking zeroes. JSON decimals are parsed losslessly, duplicate object keys are rejected, and source values are accepted only when conversion to the API number format preserves a unique decimal value. Safety-contract equality and notional multiplication use exact, unrounded decimal values; ledger verification requires the exact sequence primary-key and text-column schema and rejects whitespace-only event identities. Commit, tree, seal, and ledger digests must use canonical lowercase hexadecimal in both backend and frontend validation. The raw service value for the ledger must lexically match one unambiguous `releases/<commit>/runtime/<ledger>` path without dot segments, repeated separators, whitespace-padded segments, or Unicode control, format, surrogate, private-use, or unassigned characters, and its release commit must equal the sealed experiment commit. Paper order, fill, ledger-event, experiment, and cycle-action identities use the same conservative ASCII grammar in Python and JavaScript, avoiding Unicode-database-version drift. Backend timestamp normalisation contains UTC boundary overflow, and freshness plus experiment chronology compare exact integer nanoseconds instead of truncated microseconds. Health-bearing API timestamps use a shared timezone-aware RFC 3339 subset with at most nine fractional digits; fills require valid timestamps, and structured journal cycles require a valid machine-identity action, a string-array `blockers` field, and an ASCII-digit journal-derived timestamp. Safe JSON scalar journal records and structured diagnostics that do not claim cycle keys are ignored, while malformed records that claim the cycle schema degrade aggregate health. State, ledger, and service timestamps must remain within the five-hour cycle-freshness window, while public ticker data has a 60-second limit; experiment age is instead governed by the sealed start-time and chronology contract. A non-empty SQLite WAL also degrades health rather than presenting a stale checkpoint as current. The frontend independently rechecks paper symbol, USD account currency, sealed baseline, exposure limit, leverage/side policy, ledger release, and experiment chronology correlations before either the panel or statusbar can show healthy.

The dashboard therefore does **not** poll authenticated Kraken balances or live trading endpoints. The paper engine remains the sole owner of paper-state reconciliation. The displayed seal hash is the SHA-256 of the validated seal file, not a signature or independent attestation.

## Installation

### 1. Desktop plugin

```bash
mkdir -p ~/.hermes/desktop-plugins/trading-dashboard
curl --fail --show-error --location \
  -o ~/.hermes/desktop-plugins/trading-dashboard/plugin.js \
  https://raw.githubusercontent.com/yng3/hermes-trading-dashboard/main/plugin.js
```

Reload it with **Command Palette → Reload desktop plugins**.

### 2. Backend

```bash
mkdir -p ~/.hermes/plugins/trading-dashboard/dashboard
curl --fail --show-error --location \
  -o ~/.hermes/plugins/trading-dashboard/dashboard/manifest.json \
  https://raw.githubusercontent.com/yng3/hermes-trading-dashboard/main/backend/dashboard/manifest.json
curl --fail --show-error --location \
  -o ~/.hermes/plugins/trading-dashboard/dashboard/plugin_api.py \
  https://raw.githubusercontent.com/yng3/hermes-trading-dashboard/main/backend/dashboard/plugin_api.py
```

Add `trading-dashboard` to the existing `plugins.enabled` list in
`~/.hermes/config.yaml` (keep any plugins already enabled):

```yaml
plugins:
  enabled:
    - trading-dashboard
```

Restart Hermes after installing or changing the Python backend. Frontend-only changes hot-reload.

> **Upgrade atomically:** install the frontend and backend from the same commit, then restart Hermes. Mixing the old live-account frontend/backend with this paper-only release produces a deliberately degraded compatibility view.

## Expected local integration

The full automation panel expects:

- `eth-paper-cycle.service` and `eth-paper-cycle.timer` as systemd user units;
- `LEDGER_PATH`, `KILL_SWITCH_PATH`, `PAPER_EXECUTE`, and `KRAKEN_CLI` in the service environment;
- Kraken CLI's futures-paper state at `~/.config/kraken/paper/futures_state.json`; set `KRAKEN_PAPER_STATE_PATH` in the bot service environment when overriding it (the service value takes precedence over the Hermes process environment); and
- a `runtime/forward-experiments/*.json` experiment seal alongside the ledger for provenance.

Without the bot integration, the public CoinGecko market cards still work and the automation panel reports the missing prerequisites instead of guessing.

## Backend API compatibility

The read-only monitor uses `GET /paper-dashboard`. The previous authenticated account integration is retired:

- `GET /balance` and `GET /trades` remain as inert compatibility routes that return a structured retirement error and never call private Kraken commands;
- `GET /positions` returns the normalized paper positions plus dashboard health errors; and
- `GET /summary` keeps the legacy `balance`/`positions` envelope and includes the full response under `paper_dashboard`.

Consumers should migrate directly to `/paper-dashboard`. No endpoint in this release reads spot balances, private trade history, or live orders.

## Requirements

- Hermes Agent desktop app
- Python 3.11+
- `kraken-cli` for the public futures mark-price lookup; authentication is not required by the dashboard
- Linux with systemd user services for the automation-health panel

## Verification

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python tests/test_plugin_api.py
.venv/bin/ruff check backend/dashboard/plugin_api.py tests/test_plugin_api.py
.venv/bin/ruff format --check backend/dashboard/plugin_api.py tests/test_plugin_api.py
.venv/bin/pyright backend/dashboard/plugin_api.py tests/test_plugin_api.py
node --check plugin.js
node tests/smoke_plugin.mjs
```

## License

MIT