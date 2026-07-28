/**
 * Trading Dashboard — Hermes desktop plugin
 *
 * Full page route + sidebar nav + ⌘K command + statusbar automation chip.
 * Combines public market prices with a read-only view of the local
 * ETH/Kraken automated futures-paper experiment.
 *
 * Market cards use CoinGecko asset IDs and can be extended without
 * changing the paper-automation integration.
 */

import {
  host, cn, haptic, useQuery,
  ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA, STATUSBAR_AREAS,
  Codicon, GlyphSpinner, EmptyState, ErrorState,
} from '@hermes/plugin-sdk'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'

const ID = 'trading-dashboard'
const PAPER_QUERY_MAX_AGE_MS = 30_000
const EXPERIMENT_CLOCK_SKEW_NS = 30_000_000_000n
// Default assets — users can customize via the UI
const DEFAULT_ASSETS = [
  { id: 'ethereum', symbol: 'ETH', name: 'Ethereum' },
  { id: 'bitcoin', symbol: 'BTC', name: 'Bitcoin' },
]

// ── Helpers ──────────────────────────────────────────────────────────

function fmtUsd(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function fmtNum(n, decimals) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
  return n.toFixed(decimals ?? 6)
}

function isRecord(value) {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function recordArray(value) {
  return Array.isArray(value) && value.every(isRecord) ? value : null
}

function stringArray(value) {
  return Array.isArray(value) && value.every(item => typeof item === 'string') ? value : null
}

function nonEmptyText(value) {
  return typeof value === 'string' && value.trim().length > 0 && !/\p{C}/u.test(value)
}

function validIdentity(value) {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@/+_-]*$/.test(value)
}

function validFuturesSymbol(value) {
  return typeof value === 'string' && /^PF_[A-Z0-9]{3,24}$/.test(value)
}

function validProtectiveClientOrderId(value) {
  return typeof value === 'string'
    && /^ethbot-stop-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}

function apiText(value, fallback = '—') {
  if (typeof value === 'string') return value || fallback
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
}

function apiNumber(value) {
  return typeof value === 'number'
    && Number.isFinite(value)
    && Math.abs(value) <= Number.MAX_SAFE_INTEGER
    ? value
    : null
}

function paperQueryFresh(dataUpdatedAt, now = Date.now()) {
  const updatedAt = apiNumber(dataUpdatedAt)
  return updatedAt != null
    && updatedAt > 0
    && updatedAt <= now
    && now - updatedAt <= PAPER_QUERY_MAX_AGE_MS
}

function upper(value, fallback = 'UNKNOWN') {
  return typeof value === 'string' && value ? value.toUpperCase() : fallback
}

function assetFromContract(symbol) {
  if (typeof symbol !== 'string') return 'ASSET'
  return symbol.replace(/^PF_/, '').replace(/USD$/, '') || 'ASSET'
}

function sameNumber(left, right) {
  const a = apiNumber(left)
  const b = apiNumber(right)
  return a != null && b != null && a === b
}

function nonNegativeNumber(value) {
  const numeric = apiNumber(value)
  return numeric != null && numeric >= 0
}

function validTimestamp(value) {
  if (typeof value !== 'string') return false
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value)
  if (!match) return false
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6])
  const zoneHour = match[9] == null ? 0 : Number(match[9])
  const zoneMinute = match[10] == null ? 0 : Number(match[10])
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return year >= 1
    && month >= 1 && month <= 12
    && day >= 1 && day <= daysInMonth[month - 1]
    && hour <= 23 && minute <= 59 && second <= 59
    && zoneHour <= 23 && zoneMinute <= 59
}

function timestampNanoseconds(value) {
  if (!validTimestamp(value)) return null
  const fraction = /\.(\d{1,9})(?=Z|[+-]\d{2}:\d{2}$)/.exec(value)?.[1] ?? ''
  const wholeSecond = value.replace(
    /\.\d{1,9}(?=Z|[+-]\d{2}:\d{2}$)/,
    '',
  )
  const parsed = Date.parse(wholeSecond)
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) return null
  return BigInt(parsed) * 1_000_000n + BigInt(fraction.padEnd(9, '0') || '0')
}

function canonicalReleaseFromLedgerPath(value) {
  if (!nonEmptyText(value) || !value.startsWith('/') || value.includes('\\')) return null
  const parts = value.split('/')
  if (parts[0] !== '' || parts.slice(1).some(part => (
    !nonEmptyText(part)
    || part !== part.trim()
    || part === '.'
    || part === '..'
  ))) return null

  const segments = parts.slice(1)
  const releaseIndexes = segments
    .map((part, index) => part === 'releases' ? index : -1)
    .filter(index => index >= 0)
  if (releaseIndexes.length !== 1) return null
  const releaseIndex = releaseIndexes[0]
  if (segments.length !== releaseIndex + 4 || segments[releaseIndex + 2] !== 'runtime') return null
  const release = segments[releaseIndex + 1]
  return /^[0-9a-f]{40}$/.test(release) ? release : null
}

function protectiveOrderFor(position, orders) {
  if (!isRecord(position) || position.protected !== true) return null
  const protectiveIds = stringArray(position.protective_order_ids)
  if (!protectiveIds || protectiveIds.length !== 1 || !validIdentity(protectiveIds[0])) return null
  const expectedSide = position.side === 'long' ? 'short' : position.side === 'short' ? 'long' : null
  if (
    !expectedSide
    || !validFuturesSymbol(position.symbol)
    || !(apiNumber(position.size) > 0)
    || !(apiNumber(position.entry_price) > 0)
    || !sameNumber(position.leverage, 1)
  ) return null
  return orders.find(order => (
    isRecord(order)
    && validIdentity(order.id)
    && order.id === protectiveIds[0]
    && order.symbol === position.symbol
    && order.side === expectedSide
    && order.status === 'open'
    && order.order_type === 'stop'
    && validProtectiveClientOrderId(order.client_order_id)
    && order.trigger_signal === 'mark'
    && order.reduce_only === true
    && order.price === null
    && sameNumber(order.filled_size, 0)
    && apiNumber(order.size) > 0
    && sameNumber(order.size, position.size)
    && sameNumber(order.leverage, 1)
    && apiNumber(order.stop_price) > 0
    && apiNumber(position.entry_price) > 0
    && (position.side === 'long'
      ? apiNumber(order.stop_price) < apiNumber(position.entry_price)
      : apiNumber(order.stop_price) > apiNumber(position.entry_price))
  )) || null
}

function paperDashboardContract(data) {
  const invalid = {
    shapeValid: false,
    healthy: false,
    positions: [],
    orders: [],
    fills: [],
    cycles: [],
    recentEvents: [],
    warnings: [],
    matchedProtectiveOrders: [],
    protectionOk: false,
    timerOk: false,
    ledgerOk: false,
  }
  if (
    !isRecord(data)
    || !isRecord(data.automation)
    || !isRecord(data.paper)
    || !isRecord(data.paper.account)
    || !isRecord(data.paper.compliance)
    || !isRecord(data.paper.market)
    || !isRecord(data.paper.protection)
    || !isRecord(data.paper.leverage_preferences)
    || !isRecord(data.ledger)
    || !isRecord(data.experiment)
  ) return invalid

  const automation = data.automation
  const paper = data.paper
  const account = paper.account
  const compliance = paper.compliance
  const market = paper.market
  const protection = paper.protection
  const ledger = data.ledger
  const experiment = data.experiment
  const positions = recordArray(paper.positions)
  const orders = recordArray(paper.orders)
  const fills = recordArray(paper.fills)
  const history = recordArray(paper.history)
  const cycles = recordArray(data.cycles)
  const recentEvents = recordArray(ledger.recent_events)
  const warnings = stringArray(data.errors)
  const validationErrors = stringArray(paper.validation_errors)
  const allowedSides = stringArray(experiment.allowed_sides)
  if (
    !positions || !orders || !fills || !history || !cycles || !recentEvents
    || !warnings || !validationErrors || !allowedSides
  ) return invalid

  const matchedProtectiveOrders = positions.map(position => protectiveOrderFor(position, orders))
  const matchedProtectiveIds = matchedProtectiveOrders.filter(Boolean).map(order => order.id)
  const protectionOk = compliance.protected === true && (
    positions.length === 0
      ? orders.length === 0
      : orders.length === positions.length
        && matchedProtectiveOrders.every(Boolean)
        && new Set(matchedProtectiveIds).size === positions.length
  )
  const timerOk = automation.timer_enabled === true
    && automation.timer_active === true
    && automation.timestamps_fresh === true
  const ledgerCount = apiNumber(ledger.event_count)
  const ledgerOk = ledger.chain_valid === true
    && ledger.quick_check === 'ok'
    && ledger.healthy === true
    && /^[0-9a-f]{64}$/.test(apiText(ledger.head_hash, ''))
    && Number.isInteger(ledgerCount)
    && ledgerCount > 0
    && validTimestamp(ledger.last_event_at)
  const accountValid = account.currency === 'USD'
    && apiNumber(account.starting_collateral) > 0
    && apiNumber(account.collateral) != null
    && apiNumber(account.equity) != null
    && apiNumber(account.net_pnl) != null
    && apiNumber(account.pnl_pct) != null
    && apiNumber(account.unrealized_pnl) != null
    && nonNegativeNumber(account.exposure_usd)
    && nonNegativeNumber(account.fees_paid)
  const marketValid = validFuturesSymbol(market.symbol)
    && apiNumber(market.mark_price) > 0
    && apiNumber(market.index_price) > 0
    && validTimestamp(market.server_time)
  const positionsValid = positions.length <= 1 && positions.every(position => (
    validFuturesSymbol(position.symbol)
    && position.side === 'long'
    && apiNumber(position.size) > 0
    && apiNumber(position.entry_price) > 0
    && apiNumber(position.mark_price) > 0
    && apiNumber(position.unrealized_pnl) != null
    && apiNumber(position.notional_usd) > 0
    && sameNumber(position.leverage, 1)
    && position.protected === true
    && stringArray(position.protective_order_ids)?.length === 1
    && validIdentity(position.protective_order_ids[0])
  ))
  const ordersValid = orders.every(order => (
    validIdentity(order.id)
    && validFuturesSymbol(order.symbol)
    && (order.side === 'long' || order.side === 'short')
    && apiNumber(order.size) > 0
    && nonNegativeNumber(order.filled_size)
    && typeof order.reduce_only === 'boolean'
    && apiNumber(order.leverage) > 0
  ))
  const fillsValid = fills.every(fill => (
    validIdentity(fill.id)
    && validIdentity(fill.order_id)
    && validFuturesSymbol(fill.symbol)
    && (fill.side === 'long' || fill.side === 'short')
    && apiNumber(fill.size) > 0
    && apiNumber(fill.price) > 0
    && nonNegativeNumber(fill.fee)
    && validTimestamp(fill.filled_at)
  ))
  const cyclesValid = cycles.every(cycle => (
    validIdentity(cycle.action)
    && validTimestamp(cycle.occurred_at)
    && stringArray(cycle.blockers) != null
  ))
  const recentEventsValid = recentEvents.every(event => (
    Number.isInteger(apiNumber(event.sequence))
    && apiNumber(event.sequence) > 0
    && validIdentity(event.event_id)
    && validIdentity(event.event_type)
    && validTimestamp(event.occurred_at)
    && /^[0-9a-f]{64}$/.test(apiText(event.event_hash, ''))
  ))
  const preferences = Object.entries(paper.leverage_preferences)
  const preferencesValid = preferences.length > 0 && preferences.every(([symbol, leverage]) => (
    validFuturesSymbol(symbol) && sameNumber(leverage, 1)
  ))
  const complianceOk = compliance.paper_only === true
    && compliance.long_or_flat === true
    && compliance.max_one_position === true
    && compliance.exactly_one_x === true
    && compliance.protected === true
  const protectionCountsValid = Number.isInteger(apiNumber(protection.covered_positions))
    && protection.covered_positions === positions.length
    && Number.isInteger(apiNumber(protection.position_count))
    && protection.position_count === positions.length
    && Number.isInteger(apiNumber(protection.open_order_count))
    && protection.open_order_count === orders.length
  const experimentValid = experiment.provenance_valid === true
    && experiment.contract_valid === true
    && validIdentity(experiment.experiment_id)
    && /^[0-9a-f]{64}$/.test(apiText(experiment.seal_file_sha256, ''))
    && /^[0-9a-f]{40}$/.test(apiText(experiment.commit, ''))
    && /^[0-9a-f]{40}$/.test(apiText(experiment.tree, ''))
    && validFuturesSymbol(experiment.symbol)
    && apiNumber(experiment.account_baseline_usd) > 0
    && apiNumber(experiment.max_notional_usd) > 0
    && sameNumber(experiment.leverage, 1)
    && allowedSides.length === 1
    && allowedSides[0] === 'long'
    && validTimestamp(experiment.start_at)
    && validTimestamp(experiment.started_at)
    && canonicalReleaseFromLedgerPath(experiment.ledger_path) != null
  const generatedAtNs = timestampNanoseconds(data.generated_at)
  const experimentStartNs = timestampNanoseconds(experiment.start_at)
  const experimentStartedNs = timestampNanoseconds(experiment.started_at)
  const reconciledAtNs = timestampNanoseconds(paper.last_reconciled_at)
  const ledgerEventAtNs = timestampNanoseconds(ledger.last_event_at)
  const observedSymbols = [
    market.symbol,
    ...positions.map(position => position.symbol),
    ...preferences.map(([symbol]) => symbol),
  ]
  const experimentCorrelationsValid = observedSymbols.length > 0
    && observedSymbols.every(symbol => symbol === experiment.symbol)
    && sameNumber(account.starting_collateral, experiment.account_baseline_usd)
    && apiNumber(account.exposure_usd) <= apiNumber(experiment.max_notional_usd)
    && preferences.every(([, leverage]) => sameNumber(leverage, experiment.leverage))
    && positions.every(position => (
      sameNumber(position.leverage, experiment.leverage)
      && allowedSides.includes(position.side)
    ))
    && canonicalReleaseFromLedgerPath(experiment.ledger_path) === experiment.commit
    && experiment.start_at === experiment.started_at
    && generatedAtNs != null
    && experimentStartNs != null
    && experimentStartedNs === experimentStartNs
    && experimentStartNs <= generatedAtNs + EXPERIMENT_CLOCK_SKEW_NS
    && reconciledAtNs != null
    && reconciledAtNs + EXPERIMENT_CLOCK_SKEW_NS >= experimentStartNs
    && ledgerEventAtNs != null
    && ledgerEventAtNs + EXPERIMENT_CLOCK_SKEW_NS >= experimentStartNs
  const automationValid = automation.healthy === true
    && automation.read_only_dashboard === true
    && timerOk
    && automation.execution_enabled === true
    && automation.kill_switch_armed === false
    && automation.service_result === 'success'
    && ['', '0', 0].includes(automation.service_exit_status)
    && /^[0-9a-f]{40}$/.test(apiText(automation.release, ''))
    && automation.release === experiment.commit

  return {
    shapeValid: true,
    healthy: data.healthy === true
      && data.read_only === true
      && validTimestamp(data.generated_at)
      && automationValid
      && paper.valid === true
      && paper.mode === 'futures_paper'
      && validTimestamp(paper.last_reconciled_at)
      && validTimestamp(paper.state_updated_at)
      && validationErrors.length === 0
      && warnings.length === 0
      && accountValid
      && marketValid
      && positionsValid
      && ordersValid
      && fillsValid
      && cyclesValid
      && recentEventsValid
      && preferencesValid
      && complianceOk
      && protectionCountsValid
      && protectionOk
      && ledgerOk
      && experimentValid
      && experimentCorrelationsValid,
    positions,
    orders,
    fills,
    cycles,
    recentEvents,
    warnings,
    matchedProtectiveOrders,
    protectionOk,
    timerOk,
    ledgerOk,
  }
}

// ── Asset management ─────────────────────────────────────────────────

function getAssets() {
  try {
    const stored = host.state && JSON.parse(localStorage.getItem('hermes.plugin.trading-dashboard.assets') || 'null')
    if (stored && Array.isArray(stored) && stored.length > 0) return stored
  } catch {}
  return DEFAULT_ASSETS
}

// ── Data hooks ───────────────────────────────────────────────────────

function useAssetPrices(assets) {
  const ids = assets.map(a => a.id).join(',')
  return useQuery({
    queryKey: [ID, 'prices', ids],
    queryFn: async () => {
      const res = await fetch(`https://api.coingecko.com/api/v3/simple/price?ids=${ids}&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true&include_market_cap=true`)
      if (!res.ok) throw new Error(`CoinGecko ${res.status}`)
      const data = await res.json()
      const results = {}
      for (const asset of assets) {
        const coin = data[asset.id]
        if (coin) {
          results[asset.id] = {
            price: coin.usd,
            change24h: coin.usd_24h_change,
            volume24h: coin.usd_24h_vol,
            marketCap: coin.usd_market_cap,
            fetchedAt: Date.now(),
          }
        }
      }
      return results
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
}

function usePaperDashboard() {
  return useQuery({
    queryKey: ['trading-dashboard', 'paper-automation'],
    queryFn: async () => {
      if (restRef) {
        return restRef('/paper-dashboard')
      }
      throw new Error('Backend not connected')
    },
    refetchInterval: 15_000,
    staleTime: 5_000,
    retry: 1,
  })
}

// Module-level ref for ctx.rest, set in register()
let restRef = null

// ── Components ───────────────────────────────────────────────────────

function StatCard({ label, value, sub, accent }) {
  const safeSub = apiText(sub, '')
  return jsxs('div', {
    className: cn(
      'flex flex-col gap-1 rounded-lg border px-4 py-3',
      'border-(--ui-stroke-secondary) bg-(--chrome-surface)'
    ),
    children: [
      jsx('div', {
        className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)',
        children: apiText(label, ''),
      }),
      jsx('div', {
        className: cn('text-xl font-semibold tabular-nums', accent),
        children: apiText(value),
      }),
      safeSub ? jsx('div', {
        className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
        children: safeSub,
      }) : null,
    ],
  })
}

function AssetCard({ asset, priceData }) {
  if (!priceData) {
    return jsxs('div', {
      className: cn(
        'flex flex-col gap-3 rounded-lg border px-4 py-4',
        'border-(--ui-stroke-secondary) bg-(--chrome-surface)'
      ),
      children: [
        jsxs('div', { className: 'flex items-center gap-2', children: [
          jsx('div', { className: 'font-medium text-sm', children: apiText(asset.name, 'Unknown asset') }),
          jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: apiText(asset.symbol, '') }),
        ]}),
        jsx('div', { className: 'text-(--ui-text-tertiary) text-sm', children: 'Loading...' }),
      ],
    })
  }

  const change = apiNumber(priceData.change24h)
  const isUp = change == null ? null : change >= 0
  const changeColor = isUp == null
    ? 'text-(--ui-text-quaternary)'
    : isUp ? 'text-emerald-500' : 'text-red-500'

  return jsxs('div', {
    className: cn(
      'flex flex-col gap-3 rounded-lg border px-4 py-4',
      'border-(--ui-stroke-secondary) bg-(--chrome-surface)'
    ),
    children: [
      // Header with name + symbol
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('div', { className: 'font-medium text-sm', children: apiText(asset.name, 'Unknown asset') }),
          jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: apiText(asset.symbol, '') }),
        ],
      }),

      // Big price display
      jsxs('div', {
        className: 'flex items-baseline gap-3',
        children: [
          jsx('div', {
            className: 'text-2xl font-bold tabular-nums',
            children: fmtUsd(priceData.price),
          }),
          jsxs('div', {
            className: cn('flex items-center gap-1 text-sm font-medium tabular-nums', changeColor),
            children: [
              isUp == null ? null : jsx(Codicon, { name: isUp ? 'arrow-up' : 'arrow-down', size: 12 }),
              jsx('span', { children: fmtPct(change) }),
            ],
          }),
          jsx('div', {
            className: 'ml-auto text-[0.6875rem] text-(--ui-text-quaternary)',
            children: '24h',
          }),
        ],
      }),

      // Stats grid
      jsxs('div', {
        className: 'grid grid-cols-2 gap-2',
        children: [
          jsx(StatCard, { label: '24h Volume', value: fmtUsd(priceData.volume24h) }),
          jsx(StatCard, { label: 'Market Cap', value: fmtUsd(priceData.marketCap) }),
        ],
      }),
    ],
  })
}

function fmtTime(value) {
  if ((typeof value !== 'string' && typeof value !== 'number') || !value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return apiText(value)
  return parsed.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

function fmtHash(value) {
  return apiText(value).slice(0, 10)
}

function humanize(value) {
  if (typeof value !== 'string' || !value) return 'Unknown'
  return value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function PaperPanel({ children, className }) {
  return jsx('div', {
    className: cn(
      'rounded-lg border border-(--ui-stroke-secondary) bg-(--chrome-surface)',
      className
    ),
    children,
  })
}

function PanelHeader({ icon, title, trailing }) {
  return jsxs('div', {
    className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3',
    children: [
      jsx(Codicon, { name: icon, size: 15 }),
      jsx('h2', { className: 'text-sm font-medium', children: apiText(title, '') }),
      trailing ? jsx('div', { className: 'ml-auto', children: trailing }) : null,
    ],
  })
}

function HealthCheck({ label, ok, value, warning }) {
  const tone = ok ? 'text-emerald-500' : warning ? 'text-amber-500' : 'text-red-500'
  return jsxs('div', {
    className: 'min-w-0 rounded-md border border-(--ui-stroke-secondary) px-3 py-2.5',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-1.5',
        children: [
          jsx('span', { className: cn('h-1.5 w-1.5 shrink-0 rounded-full', ok ? 'bg-emerald-500' : warning ? 'bg-amber-500' : 'bg-red-500') }),
          jsx('span', { className: 'truncate text-[0.6875rem] uppercase tracking-wide text-(--ui-text-quaternary)', children: apiText(label, '') }),
        ],
      }),
      jsx('div', { className: cn('mt-1 truncate text-xs font-medium', tone), children: apiText(value) }),
    ],
  })
}

function StateBadge({ tone, children }) {
  const classes = tone === 'good'
    ? ['bg-emerald-500', 'text-emerald-500']
    : tone === 'bad'
      ? ['bg-red-500', 'text-red-500']
      : tone === 'warn'
        ? ['bg-amber-500', 'text-amber-500']
        : ['bg-(--ui-text-quaternary)', 'text-(--ui-text-tertiary)']
  return jsxs('span', {
    className: cn('inline-flex items-center gap-1.5 text-[0.6875rem] font-medium', classes[1]),
    children: [
      jsx('span', { className: cn('h-1.5 w-1.5 rounded-full', classes[0]) }),
      jsx('span', { children: apiText(children) }),
    ],
  })
}

function Detail({ label, value, accent }) {
  return jsxs('div', {
    className: 'flex items-center justify-between gap-4 py-1.5 text-xs',
    children: [
      jsx('span', { className: 'text-(--ui-text-tertiary)', children: apiText(label, '') }),
      jsx('span', { className: cn('text-right font-medium tabular-nums', accent), children: apiText(value) }),
    ],
  })
}

function KrakenSection() {
  const {
    data,
    isLoading,
    isError,
    isRefetchError,
    dataUpdatedAt,
    error,
    isFetching,
    refetch,
  } = usePaperDashboard()

  if (isLoading) {
    return jsx(PaperPanel, {
      className: 'flex min-h-48 items-center justify-center',
      children: jsx(GlyphSpinner, {}),
    })
  }

  const contract = paperDashboardContract(data)
  if (isError || isRefetchError || !paperQueryFresh(dataUpdatedAt) || !contract.shapeValid) {
    return jsx(PaperPanel, {
      className: 'p-4',
      children: jsx(ErrorState, {
        title: 'Paper automation backend unavailable',
        description: `${String(error?.message || 'Backend response is incomplete')}. Restart Hermes once if this plugin was just upgraded.`,
      }),
    })
  }

  const automation = data.automation
  const paper = data.paper
  const account = paper.account
  const compliance = paper.compliance
  const market = paper.market
  const ledger = data.ledger
  const experiment = data.experiment
  const positions = contract.positions
  const orders = contract.orders
  const fills = contract.fills
  const cycles = contract.cycles
  const recentEvents = contract.recentEvents
  const position = positions[0]
  const matchedProtectiveOrders = contract.matchedProtectiveOrders
  const protectionOk = contract.protectionOk
  const protectiveOrder = matchedProtectiveOrders[0] || null
  const mark = market.mark_price
  const numericMark = apiNumber(mark)
  const numericStop = apiNumber(protectiveOrder?.stop_price)
  const stopDistance = numericMark > 0 && numericStop > 0
    ? ((numericMark - numericStop) / numericMark) * 100
    : null
  const numericPnl = apiNumber(account.net_pnl)
  const pnlPositive = numericPnl == null ? null : numericPnl >= 0
  const warnings = contract.warnings.map(error => apiText(error, 'Unknown backend error'))
  const timerOk = contract.timerOk
  const timerValue = automation.timer_enabled !== true
    ? 'Disabled'
    : automation.timer_active !== true ? 'Inactive'
      : automation.timestamps_fresh === true ? 'Scheduled' : 'Stale schedule'
  const protectionValue = positions.length
    ? protectionOk ? 'Exact stop' : 'Unprotected'
    : orders.length ? 'Orphan order' : protectionOk ? 'Not required' : 'Unverified'
  const ledgerCount = apiNumber(ledger.event_count)
  const ledgerOk = contract.ledgerOk
  const ledgerValue = ledger.quick_check && ledger.quick_check !== 'ok'
    ? 'Database failed'
    : ledger.chain_valid === false ? 'Invalid chain'
      : ledgerOk ? `${ledgerCount} valid events` : 'Unavailable'

  return jsxs(Fragment, {
    children: [
      jsx(PaperPanel, {
        className: 'overflow-hidden',
        children: jsxs(Fragment, {
          children: [
            jsxs('div', {
              className: 'flex flex-wrap items-center gap-2 border-b border-(--ui-stroke-secondary) px-4 py-3',
              children: [
                jsx(Codicon, { name: 'server-process', size: 16 }),
                jsx('div', { className: 'text-sm font-semibold', children: 'Automated Paper Execution' }),
                jsx(StateBadge, { tone: contract.healthy ? 'good' : 'bad', children: contract.healthy ? 'Healthy' : 'Attention' }),
                jsx('span', {
                  className: 'rounded border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)',
                  children: 'Read only',
                }),
                jsx('button', {
                  type: 'button',
                  className: 'ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
                  disabled: isFetching,
                  onClick: () => refetch(),
                  children: jsxs(Fragment, {
                    children: [jsx(Codicon, { name: 'refresh', size: 13 }), jsx('span', { children: isFetching ? 'Refreshing' : 'Refresh' })],
                  }),
                }),
              ],
            }),
            jsx('div', {
              className: 'grid grid-cols-2 gap-2 p-4 md:grid-cols-5',
              children: [
                jsx(HealthCheck, { label: 'Timer', ok: timerOk, value: timerValue }, 'timer'),
                jsx(HealthCheck, { label: 'Execution', ok: automation.execution_enabled === true, value: automation.execution_enabled === true ? 'Enabled' : 'Observe only' }, 'execution'),
                jsx(HealthCheck, { label: 'Kill switch', ok: automation.kill_switch_armed === false, value: automation.kill_switch_armed === true ? 'Armed' : automation.kill_switch_armed === false ? 'Disarmed' : 'Unknown' }, 'kill'),
                jsx(HealthCheck, { label: 'Protection', ok: protectionOk, value: protectionValue }, 'protection'),
                jsx(HealthCheck, { label: 'Ledger', ok: ledgerOk, value: ledgerValue }, 'ledger'),
              ],
            }),
            jsxs('div', {
              className: 'grid gap-x-8 border-t border-(--ui-stroke-secondary) px-4 py-2 sm:grid-cols-2',
              children: [
                jsx(Detail, { label: 'Last autonomous cycle', value: fmtTime(automation.last_trigger) }),
                jsx(Detail, { label: 'Next autonomous cycle', value: fmtTime(automation.next_trigger) }),
              ],
            }),
          ],
        }),
      }),

      jsx('div', {
        className: 'grid grid-cols-2 gap-3 md:grid-cols-5',
        children: [
          jsx(StatCard, { label: 'Paper Equity', value: fmtUsd(account.equity), sub: `Started ${fmtUsd(account.starting_collateral)}` }, 'equity'),
          jsx(StatCard, { label: 'Net P&L', value: fmtUsd(account.net_pnl), sub: fmtPct(account.pnl_pct), accent: pnlPositive == null ? undefined : pnlPositive ? 'text-emerald-500' : 'text-red-500' }, 'pnl'),
          jsx(StatCard, { label: 'Exposure', value: fmtUsd(account.exposure_usd), sub: positions.length > 1 ? `${positions.length} open positions` : position ? `${fmtNum(position.size, 6)} ${assetFromContract(position.symbol)}` : 'Flat' }, 'exposure'),
          jsx(StatCard, { label: `${apiText(market.symbol || position?.symbol, 'Contract')} Mark`, value: fmtUsd(mark), sub: `Index ${fmtUsd(market.index_price)}` }, 'mark'),
          jsx(StatCard, { label: 'Fees Paid', value: fmtUsd(account.fees_paid), sub: `${fills.length} fill${fills.length === 1 ? '' : 's'}` }, 'fees'),
        ],
      }),

      jsx('div', {
        className: 'grid gap-3 lg:grid-cols-2',
        children: [
          jsx(PaperPanel, {
            children: jsxs(Fragment, {
              children: [
                jsx(PanelHeader, {
                  icon: 'graph-line',
                  title: 'Current Position',
                  trailing: position ? jsx(StateBadge, { tone: position.side === 'long' ? 'good' : position.side === 'short' ? 'bad' : 'warn', children: upper(position.side) }) : jsx(StateBadge, { tone: 'muted', children: 'Flat' }),
                }),
                position
                  ? jsxs('div', {
                      className: 'px-4 py-3',
                      children: [
                        jsxs('div', {
                          className: 'mb-2 flex items-baseline gap-2',
                          children: [
                            jsx('span', { className: 'text-xl font-semibold tabular-nums', children: `${fmtNum(position.size, 6)} ${assetFromContract(position.symbol)}` }),
                            jsx('span', { className: 'text-xs text-(--ui-text-quaternary)', children: apiText(position.symbol) }),
                            jsx('span', { className: cn('ml-auto text-sm font-semibold tabular-nums', apiNumber(position.unrealized_pnl) == null ? undefined : position.unrealized_pnl >= 0 ? 'text-emerald-500' : 'text-red-500'), children: fmtUsd(position.unrealized_pnl) }),
                          ],
                        }),
                        jsx(Detail, { label: 'Entry', value: fmtUsd(position.entry_price) }),
                        jsx(Detail, { label: 'Mark', value: fmtUsd(position.mark_price) }),
                        jsx(Detail, { label: 'Current notional', value: fmtUsd(position.notional_usd) }),
                        jsx(Detail, { label: 'Leverage', value: `${fmtNum(position.leverage, 0)}×`, accent: position.leverage === 1 ? 'text-emerald-500' : 'text-red-500' }),
                        jsx(Detail, { label: 'Opened', value: fmtTime(position.created_at) }),
                      ],
                    })
                  : jsx(EmptyState, { title: 'Flat', description: 'Waiting for the next qualified long signal.' }),
              ],
            }),
          }),
          jsx(PaperPanel, {
            children: jsxs(Fragment, {
              children: [
                jsx(PanelHeader, {
                  icon: 'shield',
                  title: 'Protective Stop',
                  trailing: jsx(StateBadge, {
                    tone: positions.length
                      ? protectionOk ? 'good' : 'bad'
                      : orders.length ? 'bad' : protectionOk ? 'muted' : 'warn',
                    children: positions.length && protectionOk ? 'Open' : protectionValue,
                  }),
                }),
                protectiveOrder
                  ? jsxs('div', {
                      className: 'px-4 py-3',
                      children: [
                        jsxs('div', {
                          className: 'mb-2 flex items-baseline gap-2',
                          children: [
                            jsx('span', { className: 'text-xl font-semibold tabular-nums', children: fmtUsd(protectiveOrder.stop_price) }),
                            jsx('span', { className: 'text-xs text-(--ui-text-quaternary)', children: stopDistance == null ? '— from mark' : `${fmtPct(-stopDistance)} from mark` }),
                          ],
                        }),
                        jsx(Detail, { label: 'Order', value: protectiveOrder.id }),
                        jsx(Detail, { label: 'Trigger', value: `${humanize(protectiveOrder.trigger_signal)} price` }),
                        jsx(Detail, { label: 'Side / size', value: `${upper(protectiveOrder.side)} ${fmtNum(protectiveOrder.size, 6)}` }),
                        jsx(Detail, { label: 'Reduce only', value: protectiveOrder.reduce_only === true ? 'Yes' : 'No', accent: protectiveOrder.reduce_only === true ? 'text-emerald-500' : 'text-red-500' }),
                        jsx(Detail, { label: 'Filled', value: fmtNum(protectiveOrder.filled_size, 6) }),
                      ],
                    })
                  : jsx(EmptyState, {
                      title: position ? 'Protection missing' : orders.length ? 'Orphan order detected' : 'No stop required',
                      description: position ? 'The runtime is expected to fail closed.' : orders.length ? 'An open order exists without a matching position.' : 'A matching reduce-only stop appears with every entry.',
                    }),
              ],
            }),
          }),
        ],
      }),

      positions.length > 1
        ? jsx(PaperPanel, {
            className: 'overflow-hidden border-red-500/40',
            children: jsxs(Fragment, {
              children: [
                jsx(PanelHeader, { icon: 'warning', title: `Additional Open Positions (${positions.length - 1})` }),
                jsx('div', {
                  className: 'divide-y divide-(--ui-stroke-secondary)',
                  children: positions.slice(1).map((extra, index) => {
                    const extraProtection = protectiveOrderFor(extra, orders)
                    return jsxs('div', {
                      className: 'grid gap-1 px-4 py-3 text-xs sm:grid-cols-5',
                      children: [
                        jsx('span', { className: 'font-medium', children: apiText(extra.symbol, `Position ${index + 2}`) }),
                        jsx('span', { children: upper(extra.side) }),
                        jsx('span', { className: 'tabular-nums', children: `${fmtNum(extra.size, 6)} ${assetFromContract(extra.symbol)}` }),
                        jsx('span', { className: 'tabular-nums', children: `${fmtNum(extra.leverage, 0)}×` }),
                        jsx(StateBadge, { tone: extraProtection ? 'good' : 'bad', children: extraProtection ? `Stop ${extraProtection.id}` : 'Unprotected' }),
                      ],
                    }, typeof extra.symbol === 'string' ? extra.symbol : index)
                  }),
                }),
              ],
            }),
          })
        : null,

      jsx(PaperPanel, {
        className: 'overflow-hidden',
        children: jsxs(Fragment, {
          children: [
            jsx(PanelHeader, { icon: 'list-ordered', title: 'Paper Fills', trailing: jsx('span', { className: 'text-xs text-(--ui-text-quaternary)', children: `${fills.length} total` }) }),
            fills.length === 0
              ? jsx(EmptyState, { title: 'No fills yet', description: 'Completed paper trades will appear here.' })
              : jsx('div', {
                  className: 'overflow-x-auto',
                  children: jsxs('table', {
                    className: 'w-full text-left text-xs',
                    children: [
                      jsx('thead', {
                        className: 'border-b border-(--ui-stroke-secondary) text-(--ui-text-quaternary)',
                        children: jsx('tr', {
                          children: ['Time', 'Side', 'Size', 'Price', 'Fee', 'Order'].map(label => jsx('th', { className: 'px-4 py-2 font-medium', children: label }, label)),
                        }),
                      }),
                      jsx('tbody', {
                        children: fills.map((fill, index) => jsxs('tr', {
                          className: 'border-b border-(--ui-stroke-secondary) last:border-0',
                          children: [
                            jsx('td', { className: 'whitespace-nowrap px-4 py-2.5 text-(--ui-text-tertiary)', children: fmtTime(fill.filled_at) }),
                            jsx('td', { className: cn('px-4 py-2.5 font-medium', fill.side === 'long' ? 'text-emerald-500' : fill.side === 'short' ? 'text-red-500' : 'text-(--ui-text-tertiary)'), children: upper(fill.side) }),
                            jsx('td', { className: 'px-4 py-2.5 tabular-nums', children: fmtNum(fill.size, 6) }),
                            jsx('td', { className: 'px-4 py-2.5 tabular-nums', children: fmtUsd(fill.price) }),
                            jsx('td', { className: 'px-4 py-2.5 tabular-nums text-(--ui-text-tertiary)', children: fmtUsd(fill.fee) }),
                            jsx('td', { className: 'px-4 py-2.5 font-mono text-[0.6875rem] text-(--ui-text-tertiary)', children: apiText(fill.order_id) }),
                          ],
                        }, typeof fill.id === 'string' ? fill.id : typeof fill.order_id === 'string' ? fill.order_id : index)),
                      }),
                    ],
                  }),
                }),
          ],
        }),
      }),

      jsx('div', {
        className: 'grid gap-3 lg:grid-cols-2',
        children: [
          jsx(PaperPanel, {
            className: 'overflow-hidden',
            children: jsxs(Fragment, {
              children: [
                jsx(PanelHeader, { icon: 'history', title: 'Recent Bot Cycles' }),
                jsx('div', {
                  className: 'divide-y divide-(--ui-stroke-secondary)',
                  children: cycles.slice(0, 6).map((cycle, index) => {
                    const blockers = stringArray(cycle.blockers) || []
                    return jsxs('div', {
                    className: 'px-4 py-2.5',
                    children: [
                      jsxs('div', {
                        className: 'flex items-center gap-2 text-xs',
                        children: [
                          jsx('span', { className: 'font-medium', children: humanize(cycle.action) }),
                          blockers.length ? jsx('span', { className: 'text-red-500', children: blockers.join(', ') }) : null,
                          jsx('span', { className: 'ml-auto whitespace-nowrap text-(--ui-text-quaternary)', children: fmtTime(cycle.occurred_at) }),
                        ],
                      }),
                      jsxs('div', {
                        className: 'mt-1 flex gap-3 text-[0.6875rem] text-(--ui-text-tertiary)',
                        children: [
                          jsx('span', { children: humanize(cycle.decision?.reason || 'runtime result') }),
                          cycle.notional_usd != null ? jsx('span', { className: 'tabular-nums', children: fmtUsd(cycle.notional_usd) }) : null,
                        ],
                      }),
                    ],
                    }, `${cycle.occurred_at}-${index}`)
                  }),
                }),
              ],
            }),
          }),
          jsx(PaperPanel, {
            className: 'overflow-hidden',
            children: jsxs(Fragment, {
              children: [
                jsx(PanelHeader, { icon: 'database', title: 'Audit Ledger', trailing: jsx('span', { className: 'font-mono text-[0.625rem] text-(--ui-text-quaternary)', children: fmtHash(ledger.head_hash) }) }),
                jsx('div', {
                  className: 'divide-y divide-(--ui-stroke-secondary)',
                  children: recentEvents.slice(0, 6).map((event, index) => jsxs('div', {
                    className: 'flex items-center gap-3 px-4 py-2.5 text-xs',
                    children: [
                      jsx('span', { className: 'w-7 shrink-0 font-mono text-(--ui-text-quaternary)', children: `#${event.sequence}` }),
                      jsx('span', { className: 'min-w-0 flex-1 truncate font-medium', children: humanize(event.event_type) }),
                      jsx('span', { className: 'shrink-0 text-(--ui-text-quaternary)', children: fmtTime(event.occurred_at) }),
                    ],
                  }, event.sequence ?? index)),
                }),
              ],
            }),
          }),
        ],
      }),

      jsx(PaperPanel, {
        className: 'px-4 py-3',
        children: jsxs('div', {
          className: 'flex flex-wrap items-center gap-x-5 gap-y-1 text-[0.6875rem] text-(--ui-text-quaternary)',
          children: [
            jsxs('span', { children: ['Experiment ', jsx('strong', { className: 'font-medium text-(--ui-text-secondary)', children: apiText(experiment.experiment_id, 'unsealed') })] }),
            jsxs('span', { children: ['Seal file SHA ', jsx('code', { children: fmtHash(experiment.seal_file_sha256) })] }),
            jsxs('span', { children: ['Release ', jsx('code', { children: fmtHash(experiment.commit || automation.release) })] }),
            jsxs('span', { children: ['Tree ', jsx('code', { children: fmtHash(experiment.tree) })] }),
            jsxs('span', { children: ['Started ', fmtTime(experiment.start_at) ] }),
            jsxs('span', { children: ['Max notional ', fmtUsd(experiment.max_notional_usd)] }),
            jsxs('span', { children: ['Ledger ', jsx('code', { children: fmtHash(ledger.head_hash) })] }),
            jsx('span', { className: 'ml-auto', children: `Updated ${fmtTime(data.generated_at)}` }),
          ],
        }),
      }),

      warnings.length
        ? jsx(PaperPanel, {
            className: 'border-amber-500/40 px-4 py-3 text-xs text-amber-500',
            children: warnings.join(' · '),
          })
        : null,
    ],
  })
}

function TradingPage() {
  const assets = getAssets()
  const { data: pricesData, isLoading, isError, error } = useAssetPrices(assets)

  return jsxs('div', {
    className: 'flex h-full flex-col gap-4 overflow-y-auto p-6',
    children: [
      // Header
      jsxs('div', {
        className: 'flex items-center gap-3',
        children: [
          jsx(Codicon, { name: 'pulse', size: 22 }),
          jsxs('div', {
            children: [
              jsx('h1', { className: 'text-lg font-semibold', children: 'Trading Dashboard' }),
              jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'Markets + automated paper execution' }),
            ],
          }),
          jsxs('div', {
            className: 'ml-auto flex items-center gap-2 text-[0.6875rem] text-(--ui-text-quaternary)',
            children: [
              jsx('div', { className: 'h-1.5 w-1.5 rounded-full bg-emerald-500' }),
              jsx('span', { children: 'Read-only monitor' }),
            ],
          }),
        ],
      }),

      // Asset cards
      isLoading
        ? jsx('div', { className: 'flex items-center justify-center py-12', children: jsx(GlyphSpinner, {}) })
        : isError
          ? jsx(ErrorState, { title: 'Price fetch failed', description: String(error?.message || error) })
          : jsxs('div', {
              className: 'grid grid-cols-2 gap-3',
              children: assets.map(function(asset) {
                return jsx(AssetCard, { asset: asset, priceData: pricesData?.[asset.id] }, asset.id)
              }),
            }),

      // Automated paper execution monitor
      jsx(KrakenSection, {}),

      // Footer
      jsx('div', {
        className: 'mt-auto pt-4 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: 'Markets: CoinGecko · PF_ETHUSD mark: public Kraken Futures · Paper state: local read-only · Auto-refresh 15s',
      }),
    ],
  })
}

// ── Statusbar chip ───────────────────────────────────────────────────

function PriceChip() {
  const { data, isError, isRefetchError, dataUpdatedAt } = usePaperDashboard()
  const contract = paperDashboardContract(data)
  const queryUsable = !isError && !isRefetchError && paperQueryFresh(dataUpdatedAt)
  const healthy = queryUsable && contract.healthy
  const price = queryUsable ? apiNumber(data?.paper?.market?.mark_price) : null
  const pnl = queryUsable ? apiNumber(data?.paper?.account?.net_pnl) : null

  return jsx('button', {
    type: 'button',
    className: cn(
      'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums',
      'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground transition-colors'
    ),
    onClick: () => {
      haptic('tap')
      host.navigate('/trading')
    },
    title: 'Open Trading Dashboard',
    children: jsxs(Fragment, {
      children: [
        jsx('span', { className: cn('h-1.5 w-1.5 rounded-full', healthy ? 'bg-emerald-500' : data ? 'bg-amber-500' : 'bg-(--ui-text-quaternary)') }),
        jsx('span', { className: 'font-medium', children: 'ETH PAPER' }),
        jsx('span', { children: price == null ? '—' : fmtUsd(price) }),
        pnl == null ? null : jsx('span', { className: pnl >= 0 ? 'text-emerald-500' : 'text-red-500', children: fmtUsd(pnl) }),
      ],
    }),
  })
}

// ── Plugin export ────────────────────────────────────────────────────

export default {
  id: ID,
  name: 'Trading Dashboard',
  defaultEnabled: true,
  register(ctx) {
    restRef = ctx.rest

    // Full page route
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      data: { path: '/trading' },
      render: () => jsx(TradingPage, {}),
    })

    // Sidebar nav entry
    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      data: { path: '/trading', label: 'Trading', codicon: 'pulse' },
    })

    // ⌘K palette command
    ctx.register({
      id: 'open',
      area: PALETTE_AREA,
      data: {
        id: 'trading-dashboard.open',
        label: 'Open Trading Dashboard',
        keywords: ['trading', 'crypto', 'price', 'bitcoin', 'ethereum', 'kraken'],
        run: () => host.navigate('/trading'),
      },
    })

    // Statusbar price chip
    ctx.register({
      id: 'price-chip',
      area: STATUSBAR_AREAS.right,
      order: 90,
      render: () => jsx(PriceChip, {}),
    })
  },
}