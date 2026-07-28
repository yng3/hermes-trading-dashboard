import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const fixtureDashboard = {
  read_only: true,
  healthy: true,
  generated_at: '2026-07-27T21:44:00Z',
  errors: [],
  automation: {
    healthy: true,
    read_only_dashboard: true,
    service_active: true,
    service_active_state: 'inactive',
    service_sub_state: 'dead',
    service_result: 'success',
    service_exit_status: '0',
    timer_enabled: true,
    timer_active: true,
    timestamps_fresh: true,
    execution_enabled: true,
    kill_switch_armed: false,
    next_trigger: 'Mon 2026-07-27 20:05:00 EDT',
    last_trigger: 'Mon 2026-07-27 16:05:00 EDT',
    release: '3036337cf656dd8c6313c0c3039745d4cb80d448',
  },
  paper: {
    valid: true,
    mode: 'futures_paper',
    validation_errors: [],
    last_reconciled_at: '2026-07-27T21:43:58Z',
    state_updated_at: '2026-07-27T21:43:58Z',
    account: {
      currency: 'USD',
      starting_collateral: 400,
      collateral: 400,
      equity: 400.15,
      net_pnl: 0.15,
      pnl_pct: 0.0375,
      unrealized_pnl: 0.148,
      exposure_usd: 73.75000000004,
      fees_paid: 0.0368,
    },
    market: {
      symbol: 'PF_ETHUSD', mark_price: 1940.7, index_price: 1940.5,
      server_time: '2026-07-27T21:43:59Z',
    },
    positions: [{
      symbol: 'PF_ETHUSD', side: 'long', size: 0.038, entry_price: 1936.8,
      mark_price: 1940.7, unrealized_pnl: 0.148, notional_usd: 73.75,
      leverage: 1, protected: true, protective_order_ids: ['FP-00003'], created_at: '2026-07-27T18:28:02Z',
    }],
    orders: [{
      id: 'FP-00003', symbol: 'PF_ETHUSD', side: 'short', size: 0.038,
      status: 'open', order_type: 'stop', stop_price: 1743.1,
      price: null, client_order_id: 'ethbot-stop-00003', trigger_signal: 'mark',
      reduce_only: true, filled_size: 0, leverage: 1,
    }],
    fills: [{
      id: 'FF-00001', order_id: 'FP-00002', symbol: 'PF_ETHUSD', side: 'long', size: 0.038,
      price: 1936.8, fee: 0.0368, filled_at: '2026-07-27T18:28:02Z',
    }],
    history: [],
    leverage_preferences: { PF_ETHUSD: 1 },
    protection: { covered_positions: 1, position_count: 1, open_order_count: 1 },
    compliance: {
      paper_only: true, long_or_flat: true, max_one_position: true,
      exactly_one_x: true, protected: true,
    },
  },
  ledger: {
    event_count: 46, quick_check: 'ok', chain_valid: true, healthy: true,
    head_hash: '28d960aa43e4cbc9b39202fc2e780f3329de942feae6cbc041fde7f33492e386',
    last_event_at: '2026-07-27T20:05:02Z',
    recent_events: [{
      sequence: 46, event_id: 'EV-00046', event_type: 'position_observed',
      occurred_at: '2026-07-27T20:05:02Z',
      event_hash: '28d960aa43e4cbc9b39202fc2e780f3329de942feae6cbc041fde7f33492e386',
    }],
  },
  cycles: [{ action: 'position_observed', blockers: [], occurred_at: '2026-07-27T20:05:02Z' }],
  experiment: {
    experiment_id: 'eth-pf-20260727-v1',
    seal_file_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
    commit: '3036337cf656dd8c6313c0c3039745d4cb80d448',
    tree: '03943ea240edd884d30e385a077774952169be4e',
    account_baseline_usd: 400,
    max_notional_usd: 75,
    leverage: 1,
    allowed_sides: ['long'],
    symbol: 'PF_ETHUSD',
    ledger_path: '/home/colton/.local/share/eth-kraken-bot/releases/3036337cf656dd8c6313c0c3039745d4cb80d448/runtime/paper-ledger.sqlite3',
    start_at: '2026-07-27T18:28:00Z',
    started_at: '2026-07-27T18:28:00Z',
    provenance_valid: true,
    contract_valid: true,
  },
}

const dashboard = process.env.PAPER_DASHBOARD_JSON
  ? JSON.parse(process.env.PAPER_DASHBOARD_JSON)
  : fixtureDashboard

const sdkImport = /import \{[\s\S]*?\} from '@hermes\/plugin-sdk'\n/
const jsxImport = "import { jsx, jsxs, Fragment } from 'react/jsx-runtime'\n"
let source = fs.readFileSync(new URL('../plugin.js', import.meta.url), 'utf8')
assert(!source.includes('detail:'), 'ErrorState uses description, not detail')
assert(!/jsx\(Codicon,\s*\{\s*icon(?:[:,])/.test(source), 'Codicon uses name, not icon')
assert(!/jsx\(EmptyState,\s*\{\s*icon:/.test(source), 'EmptyState does not accept an icon prop')
source = source.replace(sdkImport, `
const { host, cn, haptic, useQuery, ROUTES_AREA, SIDEBAR_NAV_AREA,
  PALETTE_AREA, STATUSBAR_AREAS, Codicon, GlyphSpinner, EmptyState,
  ErrorState } = globalThis.sdk
`)
source = source.replace(jsxImport, 'const { jsx, jsxs, Fragment } = globalThis.runtime\n')
source = source.replace('export default {', 'globalThis.tradingPlugin = {')

const component = (name) => (props = {}) => ({
  type: name,
  props: { ...props, children: [props.children, props.title, props.description] },
})
let paperResponse = dashboard
let paperQueryState = {
  isLoading: false,
  isError: false,
  isRefetchError: false,
  isFetching: false,
  dataUpdatedAt: Date.now(),
}
let marketResponse = {
  ethereum: { price: 1940.7, change24h: 1.2, volume24h: 1_000_000, marketCap: 200_000_000 },
  bitcoin: { price: 65_000, change24h: -0.5, volume24h: 2_000_000, marketCap: 1_000_000_000 },
}
const queryOptions = []
const restPaths = []
const context = {
  console,
  Date,
  JSON,
  Number,
  localStorage: { getItem: () => null },
  sdk: {
    host: { state: true, navigate: () => {} },
    cn: (...parts) => parts.flat(Infinity).filter(Boolean).join(' '),
    haptic: () => {},
    useQuery: (options) => {
      queryOptions.push(options)
      const { queryKey } = options
      if (queryKey.includes('paper-automation')) {
        return { data: paperResponse, ...paperQueryState, refetch: () => {} }
      }
      return {
        data: marketResponse,
        isLoading: false,
        isError: false,
      }
    },
    ROUTES_AREA: 'routes',
    SIDEBAR_NAV_AREA: 'sidebar',
    PALETTE_AREA: 'palette',
    STATUSBAR_AREAS: { right: 'statusbar-right' },
    Codicon: component('codicon'),
    GlyphSpinner: component('spinner'),
    EmptyState: component('empty'),
    ErrorState: component('error'),
  },
  runtime: {
    jsx: (type, props) => ({ type, props: props || {} }),
    jsxs: (type, props) => ({ type, props: props || {} }),
    Fragment: 'fragment',
  },
}
context.globalThis = context
vm.createContext(context)
vm.runInContext(source, context, { filename: 'plugin.js' })

const contributions = []
context.tradingPlugin.register({
  rest: (path) => {
    restPaths.push(path)
    return Promise.resolve(dashboard)
  },
  register: (entry) => contributions.push(entry),
})
assert.equal(contributions.length, 4)
assert.deepEqual(contributions.map((entry) => entry.area).sort(), ['palette', 'routes', 'sidebar', 'statusbar-right'])

function collectText(node, output = []) {
  if (node == null || typeof node === 'boolean') return output
  if (typeof node === 'string' || typeof node === 'number') {
    output.push(String(node))
    return output
  }
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, output)
    return output
  }
  if (typeof node !== 'object' || node === null || !('type' in node) || !('props' in node)) {
    throw new TypeError('non-renderable object reached a React child')
  }
  if (typeof node.type === 'function') return collectText(node.type(node.props || {}), output)
  return collectText(node.props?.children, output)
}

function materialize(node) {
  if (node == null || typeof node === 'boolean' || typeof node === 'string' || typeof node === 'number') return node
  if (Array.isArray(node)) return node.map(materialize)
  if (typeof node.type === 'function') return materialize(node.type(node.props || {}))
  return {
    type: node.type,
    props: { ...(node.props || {}), children: materialize(node.props?.children) },
  }
}

function walk(node, visitor, parent = null) {
  if (node == null || typeof node === 'boolean' || typeof node === 'string' || typeof node === 'number') return
  if (Array.isArray(node)) {
    for (const child of node) walk(child, visitor, parent)
    return
  }
  visitor(node, parent)
  walk(node.props?.children, visitor, node)
}

function directText(node) {
  const children = node?.props?.children
  if (typeof children === 'string' || typeof children === 'number') return String(children)
  return null
}

function parentClassForText(root, text) {
  let result = null
  walk(materialize(root), (node, parent) => {
    if (result == null && directText(node) === text) result = parent?.props?.className || null
  })
  return result
}

function allClasses(root) {
  const result = []
  walk(materialize(root), node => {
    if (typeof node.props?.className === 'string') result.push(node.props.className)
  })
  return result.join(' ')
}

function codiconNames(root) {
  const result = []
  walk(materialize(root), node => {
    if (node.type === 'codicon' && typeof node.props?.name === 'string') result.push(node.props.name)
  })
  return result
}

const page = contributions.find((entry) => entry.area === 'routes')
assert(page)
function renderDashboard(response) {
  paperResponse = response
  return collectText(page.render()).join(' ')
}

function renderDashboardTree(response) {
  paperResponse = response
  return page.render()
}

const rendered = renderDashboard(dashboard)
const paperQuery = queryOptions.find(options => options.queryKey.includes('paper-automation'))
assert(paperQuery, 'paper query must be registered at runtime')
assert.deepEqual(Array.from(paperQuery.queryKey), ['trading-dashboard', 'paper-automation'])
assert.equal(paperQuery.refetchInterval, 15_000)
assert.equal(paperQuery.staleTime, 5_000)
assert.equal(paperQuery.retry, 1)
await paperQuery.queryFn()
assert.deepEqual(restPaths, ['/paper-dashboard'])
for (const expected of [
  'Automated Paper Execution',
  'Current Position',
  'Protective Stop',
  'FP-00003',
  'Paper Fills',
  'Recent Bot Cycles',
  'Audit Ledger',
  `${dashboard.ledger.event_count} valid events`,
  'eth-pf-20260727-v1',
  'Seal',
  'Tree',
  'Started',
]) {
  assert(rendered.includes(expected), `missing rendered text: ${expected}`)
}

const statusbar = contributions.find((entry) => entry.area === 'statusbar-right')
assert(statusbar)
const statusText = collectText(statusbar.render()).join(' ')
assert(statusText.includes('ETH PAPER'))
assert(
  allClasses(statusbar.render()).includes('bg-emerald-500'),
  'canonical baseline must produce a green aggregate status',
)
const expectedPnl = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
}).format(dashboard.paper.account.net_pnl)
assert(statusText.includes(expectedPnl))

paperQueryState = { ...paperQueryState, isRefetchError: true }
const refetchErrorStatus = statusbar.render()
const refetchErrorPageText = collectText(page.render()).join(' ')
assert(
  !allClasses(refetchErrorStatus).includes('bg-emerald-500'),
  'retained healthy data after a failed refetch must not keep a green status chip',
)
assert(!collectText(refetchErrorStatus).join(' ').includes(expectedPnl), 'failed-refetch data must not display cached P&L')
assert(
  refetchErrorPageText.includes('Paper automation backend unavailable'),
  'main panel must reject retained healthy data after a failed refetch',
)

paperQueryState = {
  ...paperQueryState,
  isRefetchError: false,
  dataUpdatedAt: Date.now() - 31_000,
}
const staleQueryStatus = statusbar.render()
const staleQueryPageText = collectText(page.render()).join(' ')
assert(
  !allClasses(staleQueryStatus).includes('bg-emerald-500'),
  'stale cached query data must not keep a green status chip',
)
assert(!collectText(staleQueryStatus).join(' ').includes(expectedPnl), 'stale query data must not display cached P&L')
assert(
  staleQueryPageText.includes('Paper automation backend unavailable'),
  'main panel must reject stale cached query data',
)
paperQueryState = { ...paperQueryState, dataUpdatedAt: Date.now() }

const unrelatedStop = structuredClone(dashboard)
unrelatedStop.paper.positions[0].protective_order_ids = []
unrelatedStop.paper.positions[0].protected = false
unrelatedStop.paper.compliance.protected = false
unrelatedStop.paper.orders = [{
  ...unrelatedStop.paper.orders[0],
  id: 'FP-XBT-STOP',
  symbol: 'PF_XBTUSD',
}]
const unrelatedStopText = renderDashboard(unrelatedStop)
assert(unrelatedStopText.includes('Protection missing'))
assert(!unrelatedStopText.includes('FP-XBT-STOP'), 'unrelated stop must not be rendered as protection')

const contradictoryProtection = structuredClone(dashboard)
contradictoryProtection.paper.orders[0].symbol = 'PF_XBTUSD'
const contradictoryProtectionText = renderDashboard(contradictoryProtection)
assert(contradictoryProtectionText.includes('Protection missing'))
assert(!contradictoryProtectionText.includes('Exact stop'))

const nullFilledSize = structuredClone(dashboard)
nullFilledSize.paper.orders[0].filled_size = null
const nullFilledSizeText = renderDashboard(nullFilledSize)
assert(nullFilledSizeText.includes('Protection missing'))
assert(!nullFilledSizeText.includes('Exact stop'))

for (const mutate of [
  value => { value.paper.orders[0].client_order_id = null },
  value => { value.paper.orders[0].client_order_id = 'ethbot-stop-' },
  value => { value.paper.orders[0].trigger_signal = 'last' },
  value => { value.paper.orders[0].price = 1700 },
  value => { delete value.paper.orders[0].price },
  value => { value.paper.orders[0].leverage = 2 },
  value => { value.paper.orders[0].leverage = 1.0000000005 },
  value => { value.paper.orders[0].reduce_only = false },
  value => { value.paper.orders[0].stop_price = 2100 },
  value => { value.paper.orders[0].size = 0.03800000005 },
  value => { value.paper.orders[0].filled_size = 5e-13 },
  value => { value.paper.orders[0].filled_size = '0' },
  value => { value.paper.orders[0].filled_size = false },
  value => { value.paper.orders[0].filled_size = {} },
  value => { value.paper.positions[0].size = 0; value.paper.orders[0].size = 0 },
  value => { value.paper.positions[0].protective_order_ids.push({ unsafe: true }) },
  value => {
    value.paper.positions[0].size = Number.MAX_SAFE_INTEGER + 1
    value.paper.orders[0].size = Number.MAX_SAFE_INTEGER + 2
  },
  value => {
    value.paper.positions[0].protective_order_ids[0] = ''
    value.paper.orders[0].id = ''
  },
]) {
  const invalidProtection = structuredClone(dashboard)
  mutate(invalidProtection)
  const invalidProtectionText = renderDashboard(invalidProtection)
  assert(invalidProtectionText.includes('Protection missing'))
  assert(!invalidProtectionText.includes('Exact stop'))
}

for (const malformedSymbol of ['PF_ETH/USD', 'pf_ethusd', 'PF_ETHUSD\n', 'PF_']) {
  const invalidSymbol = structuredClone(dashboard)
  invalidSymbol.paper.market.symbol = malformedSymbol
  invalidSymbol.paper.positions[0].symbol = malformedSymbol
  invalidSymbol.paper.orders[0].symbol = malformedSymbol
  invalidSymbol.paper.fills[0].symbol = malformedSymbol
  invalidSymbol.paper.leverage_preferences = { [malformedSymbol]: 1 }
  invalidSymbol.experiment.symbol = malformedSymbol
  const invalidSymbolText = renderDashboard(invalidSymbol)
  assert(invalidSymbolText.includes('Attention'), `symbol grammar must reject ${JSON.stringify(malformedSymbol)}`)
  assert(!invalidSymbolText.includes('Exact stop'))
}

for (const [label, mutate] of [
  ['experiment commit', value => { value.experiment.commit = value.experiment.commit.toUpperCase() }],
  ['experiment tree', value => { value.experiment.tree = value.experiment.tree.toUpperCase() }],
  ['automation release', value => { value.automation.release = value.automation.release.toUpperCase() }],
  ['experiment seal', value => { value.experiment.seal_file_sha256 = value.experiment.seal_file_sha256.toUpperCase() }],
  ['ledger head', value => { value.ledger.head_hash = value.ledger.head_hash.toUpperCase() }],
  ['ledger event', value => { value.ledger.recent_events[0].event_hash = value.ledger.recent_events[0].event_hash.toUpperCase() }],
]) {
  const uppercaseDigest = structuredClone(dashboard)
  mutate(uppercaseDigest)
  const uppercaseDigestText = renderDashboard(uppercaseDigest)
  assert(uppercaseDigestText.includes('Attention'), `${label} must require canonical lowercase`)
  paperResponse = uppercaseDigest
  assert(
    !allClasses(statusbar.render()).includes('bg-emerald-500'),
    `${label} must not preserve green aggregate status`,
  )
}
paperResponse = dashboard

const mismatchedRelease = structuredClone(dashboard)
mismatchedRelease.automation.release = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
assert(renderDashboard(mismatchedRelease).includes('Attention'), 'release and experiment commit must match')
paperResponse = mismatchedRelease
assert(
  !allClasses(statusbar.render()).includes('bg-emerald-500'),
  'release mismatch must degrade statusbar health',
)
paperResponse = dashboard

for (const [label, mutate] of [
  ['experiment/paper symbol mismatch', value => { value.experiment.symbol = 'PF_XBTUSD' }],
  ['paper currency mismatch', value => { value.paper.account.currency = 'EUR' }],
  ['sealed baseline mismatch', value => { value.experiment.account_baseline_usd = 401 }],
  ['exposure above max notional', value => { value.experiment.max_notional_usd = 70 }],
  ['ledger release mismatch', value => {
    value.experiment.ledger_path = value.experiment.ledger_path.replace(
      value.experiment.commit,
      'a'.repeat(40),
    )
  }],
  ['noncanonical ledger path', value => {
    value.experiment.ledger_path = value.experiment.ledger_path.replace('/runtime/', '/./runtime/')
  }],
  ['future experiment start', value => {
    value.experiment.start_at = '2099-01-01T00:00:00Z'
    value.experiment.started_at = value.experiment.start_at
  }],
  ['future experiment start beyond skew by one nanosecond', value => {
    value.generated_at = '2026-07-28T00:00:00.000000000Z'
    value.experiment.start_at = '2026-07-28T00:00:30.000000001Z'
    value.experiment.started_at = value.experiment.start_at
    value.paper.last_reconciled_at = '2026-07-28T00:01:00Z'
    value.paper.state_updated_at = '2026-07-28T00:01:00Z'
    value.ledger.last_event_at = '2026-07-28T00:01:00Z'
  }],
  ['inconsistent experiment timestamps', value => {
    value.experiment.started_at = '2026-07-27T18:29:00Z'
  }],
  ['paper state predates experiment', value => {
    value.paper.last_reconciled_at = '2026-07-27T17:00:00Z'
  }],
  ['ledger predates experiment', value => {
    value.ledger.last_event_at = '2026-07-27T17:00:00Z'
  }],
]) {
  const invalidCorrelation = structuredClone(dashboard)
  mutate(invalidCorrelation)
  assert(
    renderDashboard(invalidCorrelation).includes('Attention'),
    `${label} must fail the frontend contract`,
  )
  paperResponse = invalidCorrelation
  assert(
    !allClasses(statusbar.render()).includes('bg-emerald-500'),
    `${label} must degrade statusbar health`,
  )
}
paperResponse = dashboard

for (const [label, mutate] of [
  ['protective ID format control', value => { value.paper.positions[0].protective_order_ids[0] = '\u200b' }],
  ['order ID embedded control', value => { value.paper.orders[0].id = 'FP\u0000STOP' }],
  ['fill ID format control', value => { value.paper.fills[0].id = '\u200b' }],
  ['fill order ID format control', value => { value.paper.fills[0].order_id = '\u200b' }],
  ['ledger event ID format control', value => { value.ledger.recent_events[0].event_id = '\u200b' }],
  ['ledger event type format control', value => { value.ledger.recent_events[0].event_type = '\u200b' }],
  ['experiment ID format control', value => { value.experiment.experiment_id = '\u200b' }],
  ['cycle action format control', value => { value.cycles[0].action = '\u200b' }],
]) {
  const invalidIdentity = structuredClone(dashboard)
  mutate(invalidIdentity)
  assert(renderDashboard(invalidIdentity).includes('Attention'), `${label} must fail closed`)
  paperResponse = invalidIdentity
  assert(
    !allClasses(statusbar.render()).includes('bg-emerald-500'),
    `${label} must degrade statusbar health`,
  )
}
const postUnicode14Identity = `id-${String.fromCodePoint(0x1e4d0)}`
const unsafeAsciiIdentity = 'id<bad>'
const unsafeAsciiIdentityDashboard = structuredClone(dashboard)
unsafeAsciiIdentityDashboard.experiment.experiment_id = unsafeAsciiIdentity
assert(
  renderDashboard(unsafeAsciiIdentityDashboard).includes('Attention'),
  'machine identity grammar must reject unsafe ASCII punctuation',
)
paperResponse = unsafeAsciiIdentityDashboard
assert(
  !allClasses(statusbar.render()).includes('bg-emerald-500'),
  'unsafe ASCII machine identity must degrade statusbar health',
)
paperResponse = dashboard
const trailingNewlineIdentityDashboard = structuredClone(dashboard)
trailingNewlineIdentityDashboard.experiment.experiment_id = 'id\n'
assert(
  renderDashboard(trailingNewlineIdentityDashboard).includes('Attention'),
  'machine identity grammar must reject a trailing newline',
)
paperResponse = trailingNewlineIdentityDashboard
assert(
  !allClasses(statusbar.render()).includes('bg-emerald-500'),
  'trailing-newline machine identity must degrade statusbar health',
)
paperResponse = dashboard
for (const [label, mutate] of [
  ['protective ID', value => { value.paper.positions[0].protective_order_ids[0] = postUnicode14Identity }],
  ['order ID', value => { value.paper.orders[0].id = postUnicode14Identity }],
  ['correlated protective/order IDs', value => {
    value.paper.positions[0].protective_order_ids[0] = postUnicode14Identity
    value.paper.orders[0].id = postUnicode14Identity
  }],
  ['fill ID', value => { value.paper.fills[0].id = postUnicode14Identity }],
  ['fill order ID', value => { value.paper.fills[0].order_id = postUnicode14Identity }],
  ['ledger event ID', value => { value.ledger.recent_events[0].event_id = postUnicode14Identity }],
  ['ledger event type', value => { value.ledger.recent_events[0].event_type = postUnicode14Identity }],
  ['experiment ID', value => { value.experiment.experiment_id = postUnicode14Identity }],
  ['cycle action', value => { value.cycles[0].action = postUnicode14Identity }],
]) {
  const invalidIdentity = structuredClone(dashboard)
  mutate(invalidIdentity)
  assert(
    renderDashboard(invalidIdentity).includes('Attention'),
    `${label} must use the shared ASCII identity grammar`,
  )
  paperResponse = invalidIdentity
  assert(
    !allClasses(statusbar.render()).includes('bg-emerald-500'),
    `${label} must degrade statusbar health for post-Unicode-14 code points`,
  )
}
paperResponse = dashboard

for (const [label, mutate] of [
  ['generated timestamp', value => { value.generated_at = 'not-a-timestamp' }],
  ['paper reconciliation timestamp', value => { value.paper.last_reconciled_at = 'not-a-timestamp' }],
  ['paper state timestamp', value => { value.paper.state_updated_at = '2026-02-30T00:00:00Z' }],
  ['market timestamp', value => { value.paper.market.server_time = '2026-07-28 00:00:00Z' }],
  ['fill timestamp', value => { value.paper.fills[0].filled_at = 'not-a-timestamp' }],
  ['cycle timestamp', value => { value.cycles[0].occurred_at = '2026-07-28T00:00:00' }],
  ['ledger timestamp', value => { value.ledger.last_event_at = 'not-a-timestamp' }],
  ['event timestamp', value => { value.ledger.recent_events[0].occurred_at = 'not-a-timestamp' }],
  ['experiment timestamp', value => { value.experiment.start_at = 'not-a-timestamp' }],
]) {
  const malformedTimestamp = structuredClone(dashboard)
  mutate(malformedTimestamp)
  assert(
    renderDashboard(malformedTimestamp).includes('Attention'),
    `${label} must require the shared timestamp grammar`,
  )
}

const invalidFractionalPrecision = structuredClone(dashboard)
invalidFractionalPrecision.paper.fills[0].filled_at = '2026-07-28T00:00:00.1234567890Z'
assert(
  renderDashboard(invalidFractionalPrecision).includes('Attention'),
  'timestamps with more than nine fractional digits must fail closed',
)

const nanosecondTimestamp = structuredClone(dashboard)
nanosecondTimestamp.paper.fills[0].filled_at = '2026-07-28T00:00:00.123456789Z'
paperResponse = nanosecondTimestamp
assert(
  allClasses(statusbar.render()).includes('bg-emerald-500'),
  'canonical nanosecond timestamps must remain healthy',
)
paperResponse = dashboard

for (const [label, mutate] of [
  ['empty cycle action', value => { value.cycles[0].action = '' }],
  ['non-array blockers', value => { value.cycles[0].blockers = { bad: true } }],
  ['non-string blocker', value => { value.cycles[0].blockers = [false] }],
]) {
  const malformedCycle = structuredClone(dashboard)
  mutate(malformedCycle)
  assert(renderDashboard(malformedCycle).includes('Attention'), `${label} must fail closed`)
}

const staleAutomation = structuredClone(dashboard)
staleAutomation.automation.timestamps_fresh = false
const staleAutomationText = renderDashboard(staleAutomation)
assert(staleAutomationText.includes('Stale schedule'))
assert(staleAutomationText.includes('Attention'), 'aggregate badge must reject stale scheduling')
const staleAutomationTree = renderDashboardTree(staleAutomation)
assert(!String(parentClassForText(staleAutomationTree, 'Healthy')).includes('text-emerald-500'))
paperResponse = staleAutomation
assert(!allClasses(statusbar.render()).includes('bg-emerald-500'), 'status chip must reject stale scheduling')

const stringPnl = structuredClone(dashboard)
stringPnl.paper.account.net_pnl = '0'
paperResponse = stringPnl
const stringPnlStatus = statusbar.render()
assert(!allClasses(stringPnlStatus).includes('bg-emerald-500'), 'string P&L must degrade status health')
assert(!allClasses(stringPnlStatus).includes('text-emerald-500'), 'string P&L must not receive a positive tone')

const missingMark = structuredClone(dashboard)
missingMark.paper.market.mark_price = null
missingMark.paper.positions[0].mark_price = null
missingMark.paper.positions[0].unrealized_pnl = null
missingMark.paper.positions[0].notional_usd = null
missingMark.paper.account.equity = null
missingMark.paper.account.net_pnl = null
missingMark.paper.account.pnl_pct = null
missingMark.paper.account.exposure_usd = null
const missingMarkText = renderDashboard(missingMark)
assert(missingMarkText.includes('— from mark'))
assert(!missingMarkText.includes('+0.00% from mark'))

const fillOrder = structuredClone(dashboard)
fillOrder.paper.fills = [
  { id: 'FF-NEW', order_id: 'FP-NEW', side: 'short', size: 0.01, price: 1950, fee: 0.01, filled_at: '2026-07-27T20:00:00Z' },
  { id: 'FF-OLD', order_id: 'FP-OLD', side: 'long', size: 0.01, price: 1900, fee: 0.01, filled_at: '2026-07-27T19:00:00Z' },
]
const fillOrderText = renderDashboard(fillOrder)
assert(fillOrderText.includes('FP-NEW') && fillOrderText.includes('FP-OLD'))
assert(fillOrderText.indexOf('FP-NEW') < fillOrderText.indexOf('FP-OLD'), 'newest fill must render first')
assert(!fillOrderText.includes('FF-NEW'), 'Order column must render order_id, not fill id')

const contradictoryTimer = structuredClone(dashboard)
contradictoryTimer.automation.timer_enabled = false
contradictoryTimer.automation.timer_active = true
const timerText = renderDashboard(contradictoryTimer)
assert(timerText.includes('Disabled'))
assert(!timerText.includes('Scheduled'))

const badDatabase = structuredClone(dashboard)
badDatabase.ledger.quick_check = 'corrupt'
const badDatabaseText = renderDashboard(badDatabase)
assert(badDatabaseText.includes('Database failed'))
assert(!badDatabaseText.includes('46 valid events'))

for (const badCount of [0, null, '46', 'corrupt']) {
  const invalidLedgerCount = structuredClone(dashboard)
  invalidLedgerCount.ledger.event_count = badCount
  const invalidLedgerText = renderDashboard(invalidLedgerCount)
  assert(invalidLedgerText.includes('Unavailable'))
  assert(!invalidLedgerText.includes('valid events'))
}

const staleLedger = structuredClone(dashboard)
staleLedger.ledger.healthy = false
const staleLedgerText = renderDashboard(staleLedger)
assert(staleLedgerText.includes('Unavailable'))
assert(!staleLedgerText.includes('valid events'))
assert(staleLedgerText.includes('Attention'), 'aggregate badge must reject unhealthy ledger data')

const multiplePositions = structuredClone(dashboard)
multiplePositions.paper.positions.push({
  symbol: 'PF_XBTUSD', side: 'short', size: 0.001, entry_price: 65000,
  mark_price: 64900, unrealized_pnl: 0.1, notional_usd: 64.9,
  leverage: 0, protected: false, protective_order_ids: [],
})
multiplePositions.paper.compliance.max_one_position = false
const multiplePositionsText = renderDashboard(multiplePositions)
assert(multiplePositionsText.includes('PF_ETHUSD'))
assert(multiplePositionsText.includes('PF_XBTUSD'))
assert(multiplePositionsText.includes('0×'), 'zero leverage must honor zero requested decimals')
const multiplePositionsTree = renderDashboardTree(multiplePositions)
assert(
  !String(parentClassForText(multiplePositionsTree, 'Open')).includes('text-emerald-500'),
  'aggregate protection badge must reject an unprotected second position',
)

const orphanOrder = structuredClone(dashboard)
orphanOrder.paper.positions = []
orphanOrder.paper.compliance.protected = false
const orphanOrderText = renderDashboard(orphanOrder)
assert(orphanOrderText.includes('Orphan order'))

const malformedNested = structuredClone(dashboard)
malformedNested.paper.positions = 'corrupt'
malformedNested.paper.orders = { unexpected: true }
malformedNested.paper.fills = [{ id: 'bad-fill' }]
malformedNested.paper.compliance = null
malformedNested.cycles = [{ action: null, blockers: 'not-an-array' }]
malformedNested.ledger.recent_events = { unexpected: true }
malformedNested.errors = 'not-an-array'
assert.doesNotThrow(() => renderDashboard(malformedNested))
const malformedNestedText = renderDashboard(malformedNested)
assert(malformedNestedText.includes('Paper automation backend unavailable'))
assert(!malformedNestedText.includes('Flat'), 'malformed positions must not be presented as flat')
assert(!malformedNestedText.includes('No fills'), 'malformed fills must not be presented as empty')

for (const [label, mutate] of [
  ['positions', value => { value.paper.positions = null }],
  ['orders', value => { value.paper.orders = null }],
  ['fills', value => { value.paper.fills = null }],
  ['history', value => { delete value.paper.history }],
  ['cycles', value => { value.cycles = null }],
  ['recent events', value => { value.ledger.recent_events = null }],
  ['errors', value => { value.errors = null }],
  ['paper validation errors', value => { value.paper.validation_errors = null }],
]) {
  const malformedCollection = structuredClone(dashboard)
  mutate(malformedCollection)
  const malformedCollectionText = renderDashboard(malformedCollection)
  assert(
    malformedCollectionText.includes('Paper automation backend unavailable'),
    `${label} must degrade instead of becoming an empty collection`,
  )
}

for (const [label, mutate] of [
  ['position symbol', value => { value.paper.positions[0].symbol = { unsafe: true } }],
  ['position side', value => { value.paper.positions[0].side = { unsafe: true } }],
  ['protective ID', value => { value.paper.positions[0].protective_order_ids[0] = { unsafe: true } }],
  ['order ID', value => { value.paper.orders[0].id = { unsafe: true } }],
  ['client order ID', value => { value.paper.orders[0].client_order_id = { unsafe: true } }],
  ['fill ID', value => { value.paper.fills[0].id = { unsafe: true } }],
  ['fill order ID', value => { value.paper.fills[0].order_id = { unsafe: true } }],
  ['cycle action', value => { value.cycles[0].action = { unsafe: true } }],
  ['ledger event type', value => { value.ledger.recent_events[0].event_type = { unsafe: true } }],
  ['experiment ID', value => { value.experiment.experiment_id = { unsafe: true } }],
  ['experiment commit', value => { value.experiment.commit = { unsafe: true } }],
  ['warning', value => { value.errors = [{ unsafe: true }] }],
  ['account P&L', value => { value.paper.account.net_pnl = { unsafe: true } }],
  ['market price', value => { value.paper.market.mark_price = { unsafe: true } }],
]) {
  const objectValuedChild = structuredClone(dashboard)
  mutate(objectValuedChild)
  assert.doesNotThrow(() => renderDashboard(objectValuedChild), `${label} must be sanitized`)
  const objectValuedText = renderDashboard(objectValuedChild)
  assert(
    objectValuedText.includes('Attention') || objectValuedText.includes('Paper automation backend unavailable'),
    `${label} must not preserve aggregate healthy status`,
  )
}

marketResponse = structuredClone(marketResponse)
delete marketResponse.ethereum.change24h
paperResponse = dashboard
const missingChangeTree = page.render()
assert(!codiconNames(missingChangeTree).includes('arrow-up'), 'missing 24h change must not render as a green gain')

const degradedText = renderDashboard({
  healthy: false,
  errors: ['paper state unavailable'],
  ledger: {},
  cycles: [],
  experiment: {},
})
assert(degradedText.includes('Paper automation backend unavailable'))
assert(degradedText.includes('Backend response is incomplete'))

console.log('plugin_smoke_ok contributions=4 paper_sections=9 degraded_state=ok statusbar=ok')
