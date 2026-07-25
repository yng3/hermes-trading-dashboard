# Hermes Trading Dashboard

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) desktop plugin that adds a live trading dashboard to the native desktop app.

## Features

- **Live ETH price** — polled from CoinGecko (public API, no key needed), refreshed every 60s
- **Kraken account balance** — real spot balance via `kraken-cli` backend (ETH + USD + total value)
- **Paper bot positions** — paper/shadow trading bot positions from Kraken CLI
- **Statusbar price chip** — live ETH price ticker in the bottom status bar, click to open dashboard
- **Sidebar navigation** — "Trading" entry with pulse icon
- **⌘K command** — "Open Trading Dashboard" in the command palette

## Screenshots

*(coming soon)*

## Installation

### 1. Desktop Plugin (UI)

```bash
mkdir -p ~/.hermes/desktop-plugins/trading-dashboard
curl -o ~/.hermes/desktop-plugins/trading-dashboard/plugin.js \
  https://raw.githubusercontent.com/yng3/hermes-trading-dashboard/main/plugin.js
```

Reload plugins in the Hermes desktop app: **⌘K → Reload desktop plugins**

### 2. Backend (Kraken data, optional)

If you want live Kraken balance and paper bot positions:

```bash
mkdir -p ~/.hermes/plugins/trading-dashboard/dashboard
curl -o ~/.hermes/plugins/trading-dashboard/dashboard/manifest.json \
  https://raw.githubusercontent.com/yng3/hermes-trading-dashboard/main/backend/dashboard/manifest.json
curl -o ~/.hermes/plugins/trading-dashboard/dashboard/plugin_api.py \
  https://raw.githubusercontent.com/yng3/hermes-trading-dashboard/main/backend/dashboard/plugin_api.py

# Enable the plugin backend
hermes config set plugins.enabled '["trading-dashboard"]' --force
hermes gateway restart
```

### 3. Kraken CLI (required for backend)

The backend calls `kraken-cli` locally. Install and authenticate:

```bash
# Install kraken-cli (https://github.com/krakenfx/kraken-cli)
kraken-cli auth set
```

## Requirements

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) desktop app
- For backend: `kraken-cli` installed and authenticated

## License

MIT