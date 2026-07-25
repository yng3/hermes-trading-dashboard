/**
 * Trading Dashboard — Hermes desktop plugin
 *
 * Full page route + sidebar nav + ⌘K command + statusbar price chip.
 * Supports any crypto asset via CoinGecko (public API, no key needed).
 * Optional Kraken backend for real account balance and paper positions.
 *
 * Assets are configurable via ctx.storage — users can add/remove any
 * coin that CoinGecko supports (bitcoin, ethereum, solana, etc.).
 */

import {
  host, cn, haptic, useValue, useQuery, useQueryClient, queryClient,
  ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA, STATUSBAR_AREAS,
  Codicon, GlyphSpinner, EmptyState, ErrorState, StatusDot,
  ScrollArea, Input, Button,
} from '@hermes/plugin-sdk'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'

const ID = 'trading-dashboard'

// Default assets — users can customize via the UI
const DEFAULT_ASSETS = [
  { id: 'ethereum', symbol: 'ETH', name: 'Ethereum' },
  { id: 'bitcoin', symbol: 'BTC', name: 'Bitcoin' },
]

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

function fmtNum(n, decimals) {
  if (n == null || isNaN(n)) return '—'
  return parseFloat(n).toFixed(decimals || 6)
}

// ── Asset management ─────────────────────────────────────────────────

function getAssets() {
  try {
    const stored = host.state && JSON.parse(localStorage.getItem('hermes.plugin.trading-dashboard.assets') || 'null')
    if (stored && Array.isArray(stored) && stored.length > 0) return stored
  } catch {}
  return DEFAULT_ASSETS
}

function saveAssets(assets) {
  try {
    localStorage.setItem('hermes.plugin.trading-dashboard.assets', JSON.stringify(assets))
  } catch {}
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

function useKrakenSummary() {
  return useQuery({
    queryKey: [ID, 'kraken-summary'],
    queryFn: async () => {
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

function AssetCard({ asset, priceData }) {
  if (!priceData) {
    return jsxs('div', {
      className: cn(
        'flex flex-col gap-3 rounded-lg border px-4 py-4',
        'border-(--ui-stroke-secondary) bg-(--chrome-surface)'
      ),
      children: [
        jsxs('div', { className: 'flex items-center gap-2', children: [
          jsx('div', { className: 'font-medium text-sm', children: asset.name }),
          jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: asset.symbol }),
        ]}),
        jsx('div', { className: 'text-(--ui-text-tertiary) text-sm', children: 'Loading...' }),
      ],
    })
  }

  const change = priceData.change24h ?? 0
  const isUp = change >= 0
  const changeColor = isUp ? 'text-emerald-500' : 'text-red-500'

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
          jsx('div', { className: 'font-medium text-sm', children: asset.name }),
          jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: asset.symbol }),
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
              jsx(Codicon, { icon: isUp ? 'arrow-up' : 'arrow-down', size: 12 }),
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

function KrakenSection() {
  const { data, isLoading, isError, error } = useKrakenSummary()

  const bal = data?.balance
  const balError = bal?.error
  const xeth = !balError ? parseFloat(bal?.XETH || 0) : 0
  const zusd = !balError ? parseFloat(bal?.ZUSD || 0) : 0

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

      isLoading
        ? jsx('div', { className: 'flex items-center gap-2 py-2 text-(--ui-text-tertiary)', children: jsx(GlyphSpinner, {}) })
        : balError
          ? jsx('div', { className: 'text-[0.8125rem] text-(--ui-text-tertiary)', children: `Balance: ${balError.message || 'unavailable'}` })
          : jsxs('div', {
              className: 'grid grid-cols-2 gap-3',
              children: [
                jsx(StatCard, { label: 'USD Balance', value: fmtUsd(zusd), sub: 'Cash' }),
                jsx(StatCard, { label: 'ETH Balance', value: fmtNum(xeth, 6) + ' ETH', sub: fmtUsd(xeth * 1858.6) }),
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
  const assets = getAssets()
  const { data: pricesData, isLoading, isError, error } = useAssetPrices(assets)

  return jsxs('div', {
    className: 'flex h-full flex-col gap-4 overflow-y-auto p-6',
    children: [
      // Header
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

      // Asset cards
      isLoading
        ? jsx('div', { className: 'flex items-center justify-center py-12', children: jsx(GlyphSpinner, {}) })
        : isError
          ? jsx(ErrorState, { title: 'Price fetch failed', detail: String(error?.message || error) })
          : jsxs('div', {
              className: 'grid grid-cols-2 gap-3',
              children: assets.map(function(asset) {
                return jsx(AssetCard, { asset: asset, priceData: pricesData?.[asset.id] }, asset.id)
              }),
            }),

      // Kraken section (optional — shows if backend is connected)
      jsx(KrakenSection, {}),

      // Footer
      jsx('div', {
        className: 'mt-auto pt-4 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: 'Prices: CoinGecko (public, no key) · Account: Kraken CLI (optional) · Auto-refresh 60s',
      }),
    ],
  })
}

// ── Statusbar chip ───────────────────────────────────────────────────

function PriceChip() {
  const assets = getAssets()
  const primary = assets[0] || DEFAULT_ASSETS[0]
  const { data } = useAssetPrices([primary])
  const price = data?.[primary.id]?.price

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
        jsx('span', { className: 'font-medium', children: primary.symbol }),
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