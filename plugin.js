/**
 * Trading Dashboard — Hermes desktop plugin
 *
 * Full page route + sidebar nav + ⌘K command + statusbar price chip.
 * ETH price from CoinGecko (public). Kraken balance and paper positions
 * from the plugin's Python backend (ctx.rest → /api/plugins/trading-dashboard).
 */

import {
  host, cn, haptic, useValue, useQuery, useQueryClient, queryClient,
  ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA, STATUSBAR_AREAS,
  Codicon, GlyphSpinner, EmptyState, ErrorState, StatusDot,
} from '@hermes/plugin-sdk'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'

const ID = 'trading-dashboard'

// ── Helpers ──────────────────────────────────────────────────────────

function fmtUsd(n) {
  if (n == null || isNaN(n)) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(n) {
  if (n == null || isNaN(n)) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function fmtEth(n) {
  if (n == null || isNaN(n)) return '—'
  return parseFloat(n).toFixed(6) + ' ETH'
}

// ── Data hooks ───────────────────────────────────────────────────────

function useEthPrice() {
  return useQuery({
    queryKey: [ID, 'eth-price'],
    queryFn: async () => {
      const res = await fetch('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true&include_market_cap=true')
      if (!res.ok) throw new Error(`CoinGecko ${res.status}`)
      const data = await res.json()
      const eth = data.ethereum
      return {
        price: eth.usd,
        change24h: eth.usd_24h_change,
        volume24h: eth.usd_24h_vol,
        marketCap: eth.usd_market_cap,
        fetchedAt: Date.now(),
      }
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
}

function useKrakenSummary() {
  return useQuery({
    queryKey: [ID, 'kraken-summary'],
    queryFn: async () => {
      // ctx.rest is available via the register closure, but useQuery needs
      // a stable reference. We use host.request to hit the plugin's REST
      // namespace through the gateway, or we can use the module-level restRef.
      if (restRef) return restRef('/summary')
      throw new Error('Backend not connected')
    },
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: 1,
  })
}

// Module-level ref for ctx.rest, set in register()
let restRef = null

// ── Components ───────────────────────────────────────────────────────

function StatCard({ label, value, sub, accent }) {
  return jsxs('div', {
    className: cn(
      'flex flex-col gap-1 rounded-lg border px-4 py-3',
      'border-(--ui-stroke-secondary) bg-(--chrome-surface)'
    ),
    children: [
      jsx('div', {
        className: 'text-[0.6875rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)',
        children: label,
      }),
      jsx('div', {
        className: cn('text-xl font-semibold tabular-nums', accent),
        children: value,
      }),
      sub ? jsx('div', {
        className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
        children: sub,
      }) : null,
    ],
  })
}

function EthSummary({ ethPrice }) {
  const { data, isLoading, isError, error } = useEthPrice()

  if (isLoading) {
    return jsx('div', { className: 'flex items-center justify-center py-8', children: jsx(GlyphSpinner, {}) })
  }

  if (isError) {
    return jsx(ErrorState, { title: 'Price fetch failed', detail: String(error?.message || error) })
  }

  const change = data.change24h ?? 0
  const isUp = change >= 0
  const changeColor = isUp ? 'text-emerald-500' : 'text-red-500'

  return jsxs('div', {
    className: 'flex flex-col gap-3',
    children: [
      jsxs('div', {
        className: cn(
          'flex items-baseline gap-4 rounded-lg border px-6 py-5',
          'border-(--ui-stroke-secondary) bg-(--chrome-surface)'
        ),
        children: [
          jsx('div', {
            className: 'text-3xl font-bold tabular-nums',
            children: fmtUsd(data.price),
          }),
          jsxs('div', {
            className: cn('flex items-center gap-1 text-sm font-medium tabular-nums', changeColor),
            children: [
              jsx(Codicon, { icon: isUp ? 'arrow-up' : 'arrow-down', size: 14 }),
              jsx('span', { children: fmtPct(change) }),
            ],
          }),
          jsx('div', {
            className: 'ml-auto text-[0.6875rem] text-(--ui-text-quaternary)',
            children: '24h change',
          }),
        ],
      }),
      jsxs('div', {
        className: 'grid grid-cols-3 gap-3',
        children: [
          jsx(StatCard, { label: '24h Volume', value: fmtUsd(data.volume24h), sub: 'Ethereum' }),
          jsx(StatCard, { label: 'Market Cap', value: fmtUsd(data.marketCap), sub: 'USD' }),
          jsx(StatCard, {
            label: 'Last Update',
            value: new Date(data.fetchedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            sub: 'auto-refresh 60s',
          }),
        ],
      }),
    ],
  })
}

function KrakenSection() {
  const { data, isLoading, isError, error } = useKrakenSummary()

  // Extract balance data
  const bal = data?.balance
  const balError = bal?.error
  const xeth = !balError ? parseFloat(bal?.XETH || 0) : 0
  const zusd = !balError ? parseFloat(bal?.ZUSD || 0) : 0

  // Extract positions data
  const pos = data?.positions
  const posError = pos?.error
  const positions = Array.isArray(pos) ? pos : (Array.isArray(pos?.positions) ? pos.positions : [])

  return jsxs('div', {
    className: cn(
      'flex flex-col gap-3 rounded-lg border px-4 py-4',
      'border-(--ui-stroke-secondary) bg-(--chrome-surface)'
    ),
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Codicon, { icon: 'graph', size: 16 }),
          jsx('div', { className: 'font-medium text-sm', children: 'Kraken Account' }),
          balError || posError
            ? jsx(StatusDot, { tone: 'warning', children: 'Partial' })
            : jsx(StatusDot, { tone: 'success', children: 'Connected' }),
        ],
      }),

      // Balance row
      isLoading
        ? jsx('div', { className: 'flex items-center gap-2 py-2 text-(--ui-text-tertiary)', children: jsx(GlyphSpinner, {}) })
        : balError
          ? jsx('div', { className: 'text-[0.8125rem] text-(--ui-text-tertiary)', children: `Balance: ${balError.message || 'unavailable'}` })
          : jsxs('div', {
              className: 'grid grid-cols-3 gap-3',
              children: [
                jsx(StatCard, { label: 'USD Balance', value: fmtUsd(zusd), sub: 'Cash' }),
                jsx(StatCard, { label: 'ETH Balance', value: fmtEth(xeth), sub: fmtUsd(xeth * 1858.6) }),
                jsx(StatCard, {
                  label: 'Total Value',
                  value: fmtUsd(zusd + xeth * 1858.6),
                  sub: 'Spot + Cash',
                  accent: 'text-(--ui-accent)',
                }),
              ],
            }),

      // Positions
      jsxs('div', {
        className: 'flex flex-col gap-2 pt-2',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2 text-sm font-medium text-(--ui-text-secondary)',
            children: [
              jsx(Codicon, { icon: 'terminal', size: 14 }),
              jsx('span', { children: 'Paper / Shadow Bots' }),
            ],
          }),
          posError
            ? jsx('div', { className: 'text-[0.8125rem] text-(--ui-text-tertiary)', children: `Positions: ${posError.message || 'unavailable'}` })
            : positions.length === 0
              ? jsx(EmptyState, {
                  icon: 'inbox',
                  title: 'No open positions',
                  description: 'Paper bot positions will appear here when active.',
                })
              : jsxs('div', {
                  className: 'flex flex-col gap-1.5',
                  children: positions.map(function(p, i) {
                    return jsxs('div', {
                      className: 'flex items-center gap-3 rounded border px-3 py-2 text-sm border-(--ui-stroke-secondary)',
                      children: [
                        jsx('span', { className: 'font-medium', children: p.symbol || p.pair || 'Unknown' }),
                        jsx('span', {
                          className: cn('tabular-nums', p.side === 'long' || p.side === 'buy' ? 'text-emerald-500' : 'text-red-500'),
                          children: (p.side || p.direction || '—').toUpperCase(),
                        }),
                        jsx('span', { className: 'tabular-nums text-(--ui-text-tertiary)', children: fmtUsd(p.size || p.amount || p.notional) }),
                        p.unrealizedPnl != null
                          ? jsx('span', {
                              className: cn('ml-auto tabular-nums font-medium', p.unrealizedPnl >= 0 ? 'text-emerald-500' : 'text-red-500'),
                              children: fmtUsd(p.unrealizedPnl),
                            })
                          : null,
                      ],
                    }, i)
                  }),
                }),
        ],
      }),
    ],
  })
}

function TradingPage() {
  return jsxs('div', {
    className: 'flex h-full flex-col gap-4 overflow-y-auto p-6',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-3',
        children: [
          jsx(Codicon, { icon: 'pulse', size: 22 }),
          jsx('h1', { className: 'text-lg font-semibold', children: 'Trading Dashboard' }),
          jsxs('div', {
            className: 'ml-auto flex items-center gap-2 text-[0.6875rem] text-(--ui-text-quaternary)',
            children: [
              jsx('div', { className: 'h-1.5 w-1.5 rounded-full bg-emerald-500' }),
              jsx('span', { children: 'Live' }),
            ],
          }),
        ],
      }),

      jsxs('div', {
        className: 'flex flex-col gap-3',
        children: [
          jsx('div', { className: 'text-sm font-medium text-(--ui-text-secondary)', children: 'Ethereum' }),
          jsx(EthSummary, {}),
        ],
      }),

      jsx(KrakenSection, {}),

      jsx('div', {
        className: 'mt-auto pt-4 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: 'ETH: CoinGecko (public) · Account: Kraken CLI (local) · Auto-refresh 30-60s',
      }),
    ],
  })
}

// ── Statusbar chip ───────────────────────────────────────────────────

function PriceChip() {
  const { data } = useEthPrice()
  const price = data?.price

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
        jsx('span', { className: 'font-medium', children: 'ETH' }),
        jsx('span', { children: price != null ? fmtUsd(price) : '—' }),
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
    // Store ctx.rest reference for useQuery
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
        keywords: ['trading', 'eth', 'crypto', 'price', 'kraken'],
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