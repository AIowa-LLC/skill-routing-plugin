/**
 * Contract test: skill-owner-routing desktop plugin loads against stub SDK,
 * registers the SPEC-2 surface (V1-V5), i18n bundles nest correctly, and the
 * source obeys the hard rules (imports, no JSX syntax, no hardcoded colors).
 * Run: node tests/desktop-plugin-contract.mjs
 */
import { register } from 'node:module'
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const PLUGIN_PATH = join(here, '..', 'desktop', 'plugin.js')
const SOURCE = readFileSync(PLUGIN_PATH, 'utf8')

let failures = 0
function check(ok, label, extra) {
  if (ok) console.log('  ok  ' + label)
  else { failures++; console.log('FAIL  ' + label + (extra ? ' — ' + extra : '')) }
}

// -- stub SDK matching the real contracts (verified in apps/desktop/src/sdk) --
const stubs = mkdtempSync(join(tmpdir(), 'sor-plugin-test-'))
const stubUrl = n => pathToFileURL(join(stubs, n)).href

// Strip comments before color/JSX greps so commented-out code cannot mask violations.
const noComments = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')

writeFileSync(
  join(stubs, 'sdk.mjs'),
  `
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const host = {
  navigate() {},
  notify() {},
  state: {},
  onEvent() { return () => {} }
}
export const atom = init => ({ get: () => init, set() {} })
export const useValue = () => null
export const usePluginI18n = () => key => key // IS the t function
export const queryClient = { invalidateQueries() { return Promise.resolve() } }
// C1 harness: __setQueryData overrides what useQuery returns (default: the
// legacy canned response).
let QUERY_DATA = null
export function __setQueryData(data) { QUERY_DATA = data }
export const useQuery = () => {
  const data = QUERY_DATA !== null ? QUERY_DATA : { open_count: 2, worst_severity: 'critical', rows: [], findings: [], meta: { profiles: ['frontend'], counts_by_severity: {} } }
  return { data, isLoading: false, isError: false, error: null, refetch() {} }
}
export const useMutation = () => ({ isPending: false, isError: false, error: null, mutate() {}, mutateAsync: async () => {} })
export const profileColor = () => null
export const profileColorSoft = () => 'transparent'
export const cn = (...xs) => xs.filter(Boolean).join(' ')
const passthrough = tag => Object.defineProperty(props => ({ tag, props }), 'name', { value: tag })
export const Badge = passthrough('Badge')
export const Button = passthrough('Button')
export const Codicon = passthrough('Codicon')
export const ConfirmDialog = passthrough('ConfirmDialog')
export const Dialog = passthrough('Dialog')
export const DialogContent = passthrough('DialogContent')
export const DialogDescription = passthrough('DialogDescription')
export const DialogFooter = passthrough('DialogFooter')
export const DialogHeader = passthrough('DialogHeader')
export const DialogTitle = passthrough('DialogTitle')
export const EmptyState = passthrough('EmptyState')
export const ErrorState = passthrough('ErrorState')
export const ScrollArea = passthrough('ScrollArea')
export const SearchField = passthrough('SearchField')
export const SegmentedControl = passthrough('SegmentedControl')
export const Separator = passthrough('Separator')
export const Skeleton = passthrough('Skeleton')
export const StatusDot = passthrough('StatusDot')
export const Switch = passthrough('Switch')
export const Tip = passthrough('Tip')
`
)
writeFileSync(
  join(stubs, 'react.mjs'),
  `
// Legacy stub behavior (component functions invoked directly by walk()):
// identical to the previous no-op stubs.
// __instance/__begin/__end/__flush drive a minimal hooks runtime for the
// C1 virtualization section: useState setters schedule re-renders,
// useEffect deps are compared and cleanups run, ref callbacks fire on the
// test's commit step.
let ACTIVE = null
const QUEUE = []
export function __instance(component) {
  return { component, cursor: 0, states: {}, memos: {}, refs: {}, effects: {}, pending: [], tree: null }
}
export function __begin(inst) { ACTIVE = inst; inst.cursor = 0 }
export function __end() { ACTIVE = null }
export function __schedule(inst) { if (!QUEUE.includes(inst)) QUEUE.push(inst) }
export function __runEffects(inst) {
  for (const e of inst.pending) {
    const prev = inst.effects[e.i]
    if (prev && prev.cleanup) prev.cleanup()
    inst.effects[e.i] = { deps: e.deps, cleanup: e.fn() || undefined }
  }
  inst.pending = []
}
export function __flush(limit = 100) {
  let guard = 0
  while (QUEUE.length && guard++ < limit) {
    const inst = QUEUE.shift()
    __begin(inst)
    try { inst.tree = inst.component() } finally { __end() }
    __runEffects(inst)
  }
  if (QUEUE.length) throw new Error('render queue did not settle')
}
export const useState = init => {
  if (!ACTIVE) return [typeof init === 'function' ? init() : init, () => {}]
  const i = ACTIVE.cursor++
  if (!(i in ACTIVE.states)) ACTIVE.states[i] = typeof init === 'function' ? init() : init
  const inst = ACTIVE
  const setter = v => {
    inst.states[i] = typeof v === 'function' ? v(inst.states[i]) : v
    __schedule(inst)
  }
  return [inst.states[i], setter]
}
export const useEffect = (fn, deps) => {
  if (!ACTIVE) return undefined
  const i = ACTIVE.cursor++
  const prev = ACTIVE.effects[i]
  const changed = !prev || !deps || deps.length !== prev.deps.length ||
    deps.some((d, j) => !Object.is(d, prev.deps[j]))
  if (changed) inst_pending_push(ACTIVE, i, fn, deps)
  return undefined
}
function inst_pending_push(inst, i, fn, deps) { inst.pending.push({ i, fn, deps }) }
export const useMemo = (fn, deps) => {
  if (!ACTIVE) return fn()
  const i = ACTIVE.cursor++
  const prev = ACTIVE.memos[i]
  const changed = !prev || !deps || deps.length !== prev.deps.length ||
    deps.some((d, j) => !Object.is(d, prev.deps[j]))
  if (changed) ACTIVE.memos[i] = { value: fn(), deps }
  return ACTIVE.memos[i].value
}
export const useRef = init => {
  if (!ACTIVE) return { current: typeof init === 'function' ? init() : init }
  const i = ACTIVE.cursor++
  if (!(i in ACTIVE.refs)) ACTIVE.refs[i] = { current: typeof init === 'function' ? init() : init }
  return ACTIVE.refs[i]
}
`
)
writeFileSync(
  join(stubs, 'jsx.mjs'),
  'export const jsx = (t, p) => ({ type: t, props: p || {} })\nexport const jsxs = jsx\n'
)

const pluginUrl = pathToFileURL(PLUGIN_PATH).href
writeFileSync(
  join(stubs, 'loader.mjs'),
  `const stubs = ${JSON.stringify({
    '@hermes/plugin-sdk': stubUrl('sdk.mjs'),
    react: stubUrl('react.mjs'),
    'react/jsx-runtime': stubUrl('jsx.mjs')
  })}
const plugin = ${JSON.stringify(pluginUrl)}
export function resolve(s, c, n) { return stubs[s] ? { url: stubs[s], shortCircuit: true } : n(s, c) }
export async function load(u, c, n) {
  const r = await n(u, c)
  return u === plugin ? { format: 'module', source: r.source, shortCircuit: true } : r
}
`
)
register(pathToFileURL(join(stubs, 'loader.mjs')).href)

const m = await import(pluginUrl + '?t=' + Date.now())

// -- walk helpers over stub element trees ------------------------------------
// Stub kit components are passthroughs, so invoking a component function
// returns its element tree directly; recurse into children AND call function
// components (bounded depth guard against accidental loops).
function walk(node, visit, depth) {
  if (!node || typeof node !== 'object' || depth > 20) return
  if (typeof node.type === 'function') {
    visit(node)
    try {
      walk(node.type(node.props), visit, depth + 1)
    } catch {
      /* stub hooks limit how deep a page can render — membership above still counts */
    }
  }
  const kids = node.props && node.props.children
  ;(Array.isArray(kids) ? kids : kids ? [kids] : []).forEach(child => walk(child, visit, depth + 1))
}
function types(node) {
  const set = new Set()
  walk(node, n => set.add(n.type.name || String(n.type)), 0)
  return set
}
function flat(node) {
  const out = []
  walk(node, n => out.push(n.type))
  return out
}

console.log('\n== hard rules ==')
const importRe = /import\s[^'"]*from\s*['"]([^'"]+)['"]/g
const specifiers = []
let match
while ((match = importRe.exec(SOURCE))) specifiers.push(match[1])
check(
  specifiers.every(s => ['@hermes/plugin-sdk', 'react', 'react/jsx-runtime'].includes(s)),
  'imports only the three allowed specifiers',
  specifiers.join(', ')
)
check(!/<[A-Za-z][^>]*>/.test(noComments), 'no JSX syntax (comment-stripped)')
check(!/#[0-9a-fA-F]{3,8}\b/.test(noComments), 'no hardcoded hex colors (comment-stripped)')
check(!/rgba?\(\s*\d/.test(noComments), 'no hardcoded rgb()/rgba() colors (comment-stripped)')
check(!/\bhsl\s*\(/i.test(noComments), 'no hardcoded hsl() colors (comment-stripped)')
check(/defaultEnabled:\s*false/.test(SOURCE), 'defaultEnabled: false on the export')

console.log('\n== i18n ==')
// Mirror the SDK resolver (i18n/runtime.ts resolvePath): dot-path walk.
const resolvePath = (tree, key) =>
  key.split('.').reduce((node, part) => (node && typeof node === 'object' ? node[part] : undefined), tree)
const bundles = m.translations
check(typeof bundles.en === 'object', 'en bundle exists')
const flatKeys = Object.keys(m.translationTable.en)
const nestedOk = flatKeys.every(key => typeof resolvePath(bundles.en, key) === 'string')
check(nestedOk, 'every flat key resolves through the nested tree', 'flat registration would return undefined leaves')
check(flatKeys.some(k => k.includes('.')), 'flat authoring table uses dot-keys')
// m8: DriftFeed empty state must be i18n-keyed, and the dead statusbar key
// stays dead (source check is comment-stripped so it cannot be masked).
check(
  flatKeys.includes('empty.drift.title') && flatKeys.includes('empty.drift.desc'),
  'DriftFeed empty state uses empty.drift.* keys'
)
check(
  /\bt\('empty\.drift\.title'\)/.test(noComments) && /\bt\('empty\.drift\.desc'\)/.test(noComments),
  'DriftFeed empty state renders through t()'
)
check(!('statusbar.none' in m.translationTable.en), 'dead statusbar.none key stays removed')

console.log('\n== registration surface (V1-V5) ==')
const areas = []
m.default.register({
  rest: () => Promise.resolve({}),
  storage: { get: (_k, fb) => fb, set() {}, remove() {} },
  i18n: { register() {}, t: k => k },
  registerMany: cs => areas.push(...cs),
  onDispose() {},
  socket: () => () => {}
})
const byArea = a => areas.filter(c => c.area === a)

// -- P7 security contract: SDK-only transport --------------------------------
// SECURITY-CONTRACT §REST: desktop plugin.js must use the SDK doors
// (ctx.rest / ctx.socket) for every REST/WS call — never raw fetch, never
// window token globals. Verified statically against the comment-stripped
// source (the register() call above already proves the ctx surface works).
console.log('\n== security contract: SDK-only transport ==')
check(!/\bfetch\s*\(/.test(noComments), 'no raw fetch( calls')
check(!/XMLHttpRequest|EventSource/.test(noComments), 'no XHR / EventSource')
check(!/__HERMES_SESSION_TOKEN__|__HERMES_AUTH_REQUIRED__/.test(noComments), 'no window token globals read')
check(!/window\.(open|location)\s*=/.test(noComments), 'no navigation side effects')
check(/restRef\s*=\s*ctx\.rest/.test(noComments), 'transport binds ctx.rest (SDK door)')
check(/ctx\.socket\(\s*'\/events'/.test(noComments), 'live push binds ctx.socket (SDK door)')
check(typeof m.auditOutcome === 'function', 'auditOutcome exported')

check(areas.length === 6, 'registerMany received 6 contributions', String(areas.length))
const route = byArea('routes')
check(route.length === 1 && route[0].data.path === '/skill-ownership', 'V1 route at /skill-ownership')
const nav = byArea('sidebar.nav')
check(nav.length === 1 && nav[0].data.path === '/skill-ownership' && typeof nav[0].data.codicon === 'string', 'sidebar nav row')
check(byArea('statusBar.right').length === 1, 'V4 statusbar chip in statusBar.right')
const palette = byArea('palette')
check(palette.length === 3, 'V5 exactly 3 palette commands', String(palette.length))
check(
  palette.every(c => typeof c.data.label === 'string' && typeof c.data.run === 'function'),
  'palette commands carry label + run'
)
check(typeof route[0].render === 'function', 'route has a render function')

console.log('\n== pure helpers ==')
check(m.normalizeRow({}).state === 'clean', 'normalizeRow defaults to clean')
check(
  m.normalizeRow({ name: 'x', owner_profile: 'frontend', location: { scope: 'profile', profile: 'frontend' } }).owner === 'frontend',
  'normalizeRow reads owner_profile'
)
check(m.stateTone('clean') === 'good' && m.stateTone('duplicate') === 'bad', 'stateTone maps row states')
check(m.severityTone('critical') === 'bad' && m.severityTone('warning') === 'warn', 'severityTone maps severities')
check(
  m.postureOf({ key_present: true, core_enforces: true }) === 'core-managed' &&
    m.postureOf({ key_present: true, enabled: false }) === 'user-disabled',
  'postureOf derives from frozen policy fields'
)
check(
  m.matchesFilter({ location: { scope: 'global' } }, 'global') === true &&
    m.matchesFilter({ location: { scope: 'profile', profile: 'frontend' } }, 'frontend') === true &&
    m.matchesFilter({ location: { scope: 'profile', profile: 'frontend' } }, 'all') === true,
  'matchesFilter handles all/global/profile'
)
check(m.matchesSearch({ name: 'Web', category: 'dev', owner: null, location: { path: '/x' } }, 'web') === true, 'matchesSearch is case-insensitive')

console.log('\n== render trees (stub components are identity) ==')
const page = route[0].render()
const pageTypes = types(page)
check(pageTypes.has('PolicyPanel'), 'page contains V3 PolicyPanel')
check(pageTypes.has('SegmentedControl'), 'page contains SegmentedControl tab strip')
const chips = byArea('statusBar.right')
const chip = chips[0].render()
const chipTypes = types(chip)
check(chipTypes.has('StatusDot') && chipTypes.has('Codicon'), 'statusbar chip renders StatusDot + Codicon')

console.log('\n== C1: map virtualization attaches its scroll listener on late mount ==')
// The rows container is conditionally rendered (map.data && rows), so the
// scroll listener must attach when the container MOUNTS — not once at
// page mount. Behavioral harness: a minimal hooks runtime drives MapView
// through mount → commit(ref) → scroll → unmount against a fake viewport.
{
  const reactRT = await import(stubUrl('react.mjs'))
  const sdkRT = await import(stubUrl('sdk.mjs'))
  const RO = globalThis.ResizeObserver
  if (RO) globalThis.ResizeObserver = undefined // determinism: no RO branch
  try {
    const rows = []
    for (let i = 0; i < 200; i++) {
      rows.push({
        skill_id: 's-' + i, name: 'skill-' + i, category: 'cat', owner_profile: 'trt',
        location: { scope: 'profile', profile: 'trt', path: '/p/skill-' + i },
        state: 'clean', last_audit: null
      })
    }
    sdkRT.__setQueryData({ rows, meta: { profiles: ['trt'], last_audit_ts: null } })

    // recursive element scan that never invokes function components;
    // flattens nested arrays in children (React children semantics)
    function scan(node, visit, depth) {
      if (!node || typeof node !== 'object' || depth > 30) return
      if (Array.isArray(node)) {
        node.forEach(child => scan(child, visit, depth))
        return
      }
      visit(node)
      const kids = node.props && node.props.children
      const list = Array.isArray(kids) ? kids : kids ? [kids] : []
      list.forEach(child => scan(child, visit, depth + 1))
    }
    function instantiate(element) {
      const inst = reactRT.__instance(element.type)
      reactRT.__begin(inst)
      try { inst.tree = inst.component() } finally { reactRT.__end() }
      reactRT.__runEffects(inst)
      return inst
    }
    // commit: hand the rows-container node to whatever ref the render used
    // (function ref-callback like real React, or legacy object ref)
    function commitRef(tree, fakeNode) {
      let done = false
      scan(tree, n => {
        if (!done && n.props && 'ref' in n.props) {
          const r = n.props.ref
          if (typeof r === 'function') { r(fakeNode); done = true }
          else if (r && typeof r === 'object') { r.current = fakeNode; done = true }
        }
      }, 0)
      if (!done) throw new Error('rows container ref not found in tree')
    }
    function countRows(tree) {
      let count = 0
      scan(tree, n => { if (typeof n.type === 'function' && n.type.name === 'SkillRow') count++ }, 0)
      return count
    }

    const pageInst = instantiate(page)
    let mapEl = null
    scan(pageInst.tree, n => { if (!mapEl && typeof n.type === 'function' && n.type.name === 'MapView') mapEl = n }, 0)
    check(mapEl !== null, 'MapView present in the page tree')
    const inst = instantiate(mapEl)

    const listeners = []
    const fakeVp = {
      scrollTop: 0, clientHeight: 800,
      addEventListener(type, fn) { listeners.push(fn) },
      removeEventListener() { listeners.length = 0 }
    }
    const fakeNode = { closest: () => fakeVp, parentElement: fakeVp }

    commitRef(inst.tree, fakeNode) // rows container mounts late
    reactRT.__flush()
    check(listeners.length === 1, 'scroll listener attaches after the rows container mounts', 'listeners=' + listeners.length)

    if (listeners.length === 1) {
      const atMount = countRows(inst.tree)
      fakeVp.scrollTop = 5000 // scroll to ~row 115 of 200
      listeners[0]()
      reactRT.__flush()
      const afterScroll = countRows(inst.tree)
      check(afterScroll !== atMount && afterScroll >= 28 && afterScroll <= 34, 'scrolling re-windows the rendered rows', 'mount=' + atMount + ' after=' + afterScroll)

      let spacer = 0
      scan(inst.tree, n => {
        if (!spacer && n.props && n.props.ref === undefined && n.props.style && n.props.style.height) {
          const h = parseInt(n.props.style.height, 10)
          if (Number.isFinite(h)) spacer = Math.max(spacer, h)
        }
      }, 0)
      check(spacer > 0, 'spacer pads the unrendered range above the window', 'height=' + spacer)
    } else {
      check(false, 'scrolling re-windows the rendered rows', 'skipped: no listener attached')
      check(false, 'spacer pads the unrendered range above the window', 'skipped: no listener attached')
    }

    commitRef(inst.tree, null) // rows container unmounts
    reactRT.__flush()
    check(listeners.length === 0, 'listener detaches when the rows container unmounts', 'listeners=' + listeners.length)
    sdkRT.__setQueryData(null)
  } finally {
    if (RO) globalThis.ResizeObserver = RO
  }
}

console.log('\n== M8: audit outcome decision (timeout never reports success) ==')
check(m.auditOutcome({ state: 'done', findings_count: 3 }).failed === false, "auditOutcome: state 'done' is success")
check(m.auditOutcome({ state: 'done', findings_count: 3 }).count === 3, 'auditOutcome: done carries findings_count')
check(m.auditOutcome(null).failed === true, 'auditOutcome: missing final is failure')
check(m.auditOutcome({ state: 'failed' }).failed === true, "auditOutcome: 'failed' is failure")
check(m.auditOutcome({ state: 'running', findings_count: 2 }).failed === true, "auditOutcome: still 'running' at deadline is FAILURE, never success")
check(m.auditOutcome({ state: 'timeout' }).failed === true, "auditOutcome: 'timeout' is failure")
check(m.auditOutcome({ state: 'running' }).count === '?', "auditOutcome: count falls back to '?'")

// pollRun behavioral pins against a controllable rest stub (deadline already
// expired): a run still 'running' must surface 'timeout', a dead endpoint
// 'failed', and a terminal 'done' must pass through unchanged.
{
  let restBehavior = () => Promise.resolve({ state: 'running' })
  m.plugin.register({
    rest: (path, opts) => restBehavior(path, opts),
    storage: { get: (_k, fb) => fb, set() {}, remove() {} },
    i18n: { register() {}, t: k => k },
    registerMany() {},
    onDispose() {},
    socket: () => () => {}
  })
  const past = Date.now() - 1000
  const firstPath = await m.pollRun('run1', past)
  check(firstPath.state === 'timeout', "pollRun: still 'running' at deadline returns 'timeout', not success", JSON.stringify(firstPath))
  restBehavior = () => Promise.reject(new Error('endpoint down'))
  const dead = await m.pollRun('run2', past)
  check(dead.state === 'failed', "pollRun: unreachable endpoint at deadline returns 'failed'", JSON.stringify(dead))
  restBehavior = () => Promise.resolve({ state: 'done', findings_count: 7 })
  const done = await m.pollRun('run3', past)
  check(done.state === 'done' && done.findings_count === 7, 'pollRun: terminal done passes through', JSON.stringify(done))
}

// v0.2.1 OCR minor (F6): the ctx.socket subscription must be torn down in
// onDispose — the SDK contract documents socket() returning a disposer.
// Static source checks (comment-stripped): the disposer is captured and
// invoked inside onDispose.
console.log('\n== F6: live-push subscription teardown ==')
check(
  /disposeSocket\s*=\s*ctx\.socket\(/.test(noComments),
  'socket() disposer is captured (disposeSocket = ctx.socket(...))'
)
check(
  /onDispose\(\s*\(\)\s*=>\s*\{[^}]*disposeSocket\(\)/.test(noComments.replace(/\n/g, ' ')),
  'onDispose invokes the socket disposer'
)

console.log('\n' + (failures === 0 ? 'ALL PASS' : failures + ' FAILURE(S)'))
process.exit(failures === 0 ? 0 : 1)
