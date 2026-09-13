/**
 * skill-owner-routing — desktop UI half (SPEC-2 v1.0).
 *
 * Plain ESM loaded uncompiled: jsx()/jsxs() calls, never JSX syntax. Only
 * @hermes/plugin-sdk, react, react/jsx-runtime may be imported. Styling is
 * theme vars / kit components only — zero hardcoded colors. The REST contract
 * is the frozen SPEC-2 surface implemented by dashboard/plugin_api.py:
 *   GET /map, GET /map/{skill_id}, GET /drift, GET /drift/summary,
 *   POST /audit/run, GET /audit/runs/{run_id}, GET /policy, PUT /policy,
 *   POST /drift/{id}/resolve.
 * Findings serialize the canonical SPEC-0 Interface 3 record:
 *   {id, kind, severity, skill, expected_owner, actual, proposed_fix,
 *    discovered_at, status}; kind values render directly.
 */
import {
  Badge,
  Button,
  Codicon,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  ErrorState,
  PALETTE_AREA,
  profileColor,
  profileColorSoft,
  queryClient,
  ROUTES_AREA,
  ScrollArea,
  SearchField,
  SegmentedControl,
  Separator,
  SIDEBAR_NAV_AREA,
  Skeleton,
  STATUSBAR_AREAS,
  StatusDot,
  Switch,
  Tip,
  atom,
  cn,
  host,
  useMutation,
  usePluginI18n,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'skill-owner-routing'
const SUMMARY_POLL_MS = 15_000
const AUDIT_POLL_MS = 1500
const AUDIT_TIMEOUT_MS = 120_000
const ROW_H = 44
const OVERSCAN = 6
const GRID = 'minmax(0,2.2fr) minmax(0,1fr) minmax(0,1fr) minmax(0,1.5fr) 120px 130px'

// One-shot signal: palette "Toggle policy" expands the collapsed panel.
const $expandPolicy = atom(false)

// ---------------------------------------------------------------------------
// i18n — flat authoring, nested registration (SDK resolver walks dot-paths).
// ---------------------------------------------------------------------------

const translationTable = {
  en: {
    'nav.label': 'Skill Ownership',
    'page.title': 'Skill Ownership',
    'page.subtitle': 'Fleet map of skill ownership, drift, and routing policy',
    'tabs.map': 'Ownership map',
    'tabs.drift': 'Drift feed',
    'map.search': 'Search skills',
    'map.filter.all': 'All',
    'map.filter.global': 'Global',
    'map.lastAudit': 'Last audit',
    'map.never': 'never',
    'map.count': 'skills',
    'state.clean': 'Clean',
    'state.drifted': 'Drifted',
    'state.unowned': 'Unowned',
    'state.unknown-owner': 'Unknown owner',
    'state.duplicate': 'Duplicate',
    'drawer.frontmatter': 'Frontmatter',
    'drawer.rationale': 'Ownership rationale',
    'drawer.history': 'Route history',
    'drawer.justified': 'Hoarding justified',
    'drawer.notJustified': 'No hoarding justification recorded',
    'drawer.noRationale': 'No rationale recorded for this skill.',
    'drawer.noHistory': 'No route history recorded.',
    'drawer.close': 'Close',
    'empty.map.title': 'No skills mapped yet',
    'empty.map.desc': 'The audit scan populates this once installed.',
    'empty.filter.title': 'No matching skills',
    'empty.filter.desc': 'Adjust the search or the profile filter.',
    'empty.drift.title': 'No drift findings',
    'empty.drift.desc': 'The watchdog records drift findings here after the next audit scan.',
    'error.title': 'Could not load skill ownership data',
    'error.retry': 'Retry',
    'drift.open': 'Open',
    'drift.resolved': 'Resolved',
    'drift.resolve': 'Resolve…',
    'drift.confirm.title': 'Resolve drift finding',
    'drift.confirm.desc': 'Apply the proposed fix and mark this finding resolved. The mutation is approval-gated on the backend.',
    'drift.confirm.ok': 'Resolve',
    'drift.skill': 'Skill',
    'drift.expected': 'Expected owner',
    'drift.actual': 'Actual',
    'drift.proposed': 'Proposed fix',
    'drift.discovered': 'Discovered',
    'severity.info': 'info',
    'severity.warning': 'warning',
    'severity.critical': 'critical',
    'policy.title': 'Routing policy',
    'policy.desc': 'Writes go to skills.owner_routing in the DEFAULT profile config.',
    'policy.enabled': 'Enabled',
    'policy.requireOwner': 'Require owner metadata',
    'policy.routeDefault': 'Route from default profile',
    'policy.posture': 'Posture',
    'policy.writeFailed': 'Policy write failed',
    'posture.default-on': 'Default-ON (plugin installed)',
    'posture.user-disabled': 'User-disabled',
    'posture.core-managed': 'Core-managed (dormant)',
    'audit.run': 'Run audit now',
    'audit.running': 'Audit running…',
    'audit.started': 'Skill Ownership audit started',
    'audit.done': 'Skill Ownership audit finished',
    'audit.failed': 'Skill Ownership audit failed',
    'audit.findings': 'finding(s)',
    'status.tooltip': 'Skill ownership drift',
    'palette.open': 'Skill Ownership: Open map',
    'palette.audit': 'Skill Ownership: Run audit now',
    'palette.policy': 'Skill Ownership: Toggle policy'
  }
}

// Expand 'a.b.c' keys into the nested trees the SDK resolver walks.
function nestMessages(flat) {
  const tree = {}
  Object.keys(flat).forEach(key => {
    const parts = key.split('.')
    let node = tree
    for (let i = 0; i < parts.length - 1; i++) {
      if (!node[parts[i]] || typeof node[parts[i]] !== 'object') node[parts[i]] = {}
      node = node[parts[i]]
    }
    node[parts[parts.length - 1]] = flat[key]
  })
  return tree
}
const translations = {}
Object.keys(translationTable).forEach(locale => {
  translations[locale] = nestMessages(translationTable[locale])
})

// SDK contract: usePluginI18n(id) IS the t function — never destructure {t}.
function useI18n() {
  return { t: usePluginI18n(ID) }
}

// ---------------------------------------------------------------------------
// transport + shared helpers
// ---------------------------------------------------------------------------

let restRef = null
let storageRef = null
let i18nTRef = null

function apiRest(path, opts) {
  return restRef ? restRef(path, opts) : Promise.reject(new Error('plugin rest not ready'))
}

function invalidateAll() {
  void queryClient.invalidateQueries({ queryKey: [ID] })
}

function tStatic(key) {
  return i18nTRef ? i18nTRef(key) : key
}

function fmtTime(value) {
  const n = Number(value)
  if (!value || !Number.isFinite(n)) return ''
  const ms = n > 1e12 ? n : n * 1000
  return new Date(ms).toLocaleString()
}

// V1 row normalization — tolerant of the frozen contract's field spellings.
function normalizeRow(raw) {
  const r = raw || {}
  const loc = r.location || {}
  return {
    id: r.skill_id || r.id || r.name || '',
    name: r.name || r.skill || r.id || '',
    category: r.category || (r.frontmatter && r.frontmatter.category) || '',
    owner: r.owner_profile || r.owner || null,
    location: {
      scope: loc.scope || (loc.profile ? 'profile' : 'unknown'),
      profile: loc.profile || null,
      path: loc.path || ''
    },
    state: r.state || r.drift_state || 'clean',
    lastAudit: r.last_audit || r.last_audit_ts || null
  }
}

// Drift state -> kit StatusDot tone. unknown-owner renders hollow (below).
function stateTone(state) {
  if (state === 'clean') return 'good'
  if (state === 'drifted') return 'warn'
  if (state === 'unknown-owner') return 'warn'
  if (state === 'duplicate') return 'bad'
  return 'muted' // unowned + anything unrecognized
}

// Severity -> kit tones / Badge variants (theme semantic vars only).
function severityTone(severity) {
  if (severity === 'critical') return 'bad'
  if (severity === 'warning') return 'warn'
  return 'muted'
}
function severityBadgeVariant(severity) {
  if (severity === 'critical') return 'destructive'
  if (severity === 'warning') return 'warn'
  return 'default'
}

// Effective posture from the frozen policy fields (SPEC-1 dormancy logic).
function postureOf(policy) {
  if (!policy) return null
  if (policy.posture) return policy.posture
  if (policy.core_enforces) return 'core-managed'
  if (policy.key_present && policy.enabled === false) return 'user-disabled'
  return 'default-on'
}
function postureTone(posture) {
  if (posture === 'user-disabled') return 'muted'
  return 'good'
}

function matchesFilter(row, filter) {
  if (!filter || filter === 'all') return true
  if (filter === 'global') return row.location.scope === 'global'
  return row.location.profile === filter
}

function matchesSearch(row, query) {
  const q = (query || '').trim().toLowerCase()
  if (!q) return true
  return (
    row.name.toLowerCase().includes(q) ||
    row.category.toLowerCase().includes(q) ||
    (row.owner || '').toLowerCase().includes(q) ||
    row.location.path.toLowerCase().includes(q)
  )
}

// ---------------------------------------------------------------------------
// audit flow (palette command + page button share one path)
// ---------------------------------------------------------------------------

function pollRun(runId, deadline) {
  function tick() {
    return apiRest('/audit/runs/' + encodeURIComponent(runId)).then(
      state => {
        if (state && state.state === 'running' && Date.now() < deadline) {
          return new Promise(resolve => setTimeout(resolve, AUDIT_POLL_MS)).then(tick)
        }
        if (state && state.state === 'running') {
          // deadline expired while the run is still 'running': report the
          // timeout explicitly — never let it fall through as success
          return { state: 'timeout' }
        }
        return state
      },
      () => {
        if (Date.now() < deadline) {
          return new Promise(resolve => setTimeout(resolve, AUDIT_POLL_MS)).then(tick)
        }
        return { state: 'failed' }
      }
    )
  }
  return tick()
}

// Terminal audit-run states are 'done' and 'failed'. A run still 'running'
// at the poll deadline surfaces as 'timeout' (pollRun) — and ANY non-done
// final state (missing final, 'running', 'timeout', 'failed') must report
// failure. Only 'done' is success.
function auditOutcome(final) {
  const failed = !final || final.state !== 'done'
  const count = final && final.findings_count != null ? final.findings_count : '?'
  return { failed, count }
}

function runAuditFlow() {
  return apiRest('/audit/run', { method: 'POST', body: {} }).then(res => {
    const runId = res && res.run_id
    if (!runId) throw new Error('audit run returned no run_id')
    host.notify({ kind: 'info', message: tStatic('audit.started') })
    return pollRun(runId, Date.now() + AUDIT_TIMEOUT_MS)
  }).then(final => {
    invalidateAll()
    const { failed, count } = auditOutcome(final)
    host.notify({
      kind: failed ? 'error' : 'success',
      message: (failed ? tStatic('audit.failed') : tStatic('audit.done')) + ' — ' + count + ' ' + tStatic('audit.findings')
    })
    return final
  })
}

// ---------------------------------------------------------------------------
// shared atoms of markup
// ---------------------------------------------------------------------------

function ProfileBadge({ name }) {
  if (!name) return jsx('span', { className: 'text-xs text-(--ui-text-quaternary)', children: '—' })
  const color = profileColor(name)
  return jsx(Badge, {
    variant: 'outline',
    size: 'xs',
    style: color
      ? { color, background: profileColorSoft(color, 12), borderColor: profileColorSoft(color, 30) }
      : undefined,
    children: name
  })
}

function StateCell({ state, t }) {
  const hollow = state === 'unknown-owner'
  return jsxs('span', {
    className: 'inline-flex items-center gap-1.5 text-xs text-(--ui-text-secondary)',
    children: [
      hollow
        ? jsx('span', {
            'aria-hidden': 'true',
            style: {
              display: 'inline-block',
              width: '6px',
              height: '6px',
              borderRadius: '9999px',
              border: '1px solid var(--ui-yellow)'
            }
          })
        : jsx(StatusDot, { tone: stateTone(state) }),
      jsx('span', { children: t('state.' + state) || state })
    ]
  })
}

function SkeletonRows({ count }) {
  const rows = []
  for (let i = 0; i < count; i++) {
    rows.push(jsx(Skeleton, { key: i, className: 'h-8 w-full' }, i))
  }
  return jsx('div', { className: 'flex flex-col gap-2 p-3', children: rows })
}

// ---------------------------------------------------------------------------
// V3 — policy panel (collapsible card pinned at the top of the route)
// ---------------------------------------------------------------------------

function PolicySwitch({ label, checked, disabled, onCheckedChange }) {
  return jsxs('label', {
    className: 'flex items-center justify-between gap-3 py-1.5',
    children: [
      jsx('span', { className: 'text-sm text-(--ui-text-secondary)', children: label }),
      jsx(Switch, { checked: checked, disabled: disabled, onCheckedChange: onCheckedChange, 'aria-label': label })
    ]
  })
}

function PolicyPanel() {
  const { t } = useI18n()
  const expand = useValue($expandPolicy)
  const [collapsed, setCollapsed] = useState(() =>
    storageRef ? storageRef.get('policy.collapsed', false) : false
  )
  useEffect(() => {
    if (expand) {
      setCollapsed(false)
      $expandPolicy.set(false)
    }
  }, [expand])
  const policy = useQuery({
    queryKey: [ID, 'policy'],
    queryFn: () => apiRest('/policy'),
    staleTime: 10_000
  })
  const save = useMutation({
    mutationFn: patch => apiRest('/policy', { method: 'PUT', body: patch }),
    onSuccess: invalidateAll
  })
  const data = policy.data
  const posture = postureOf(data)

  function toggle(key) {
    if (!data) return
    save.mutate({
      enabled: key === 'enabled' ? !data.enabled : !!data.enabled,
      require_owner_metadata: key === 'require_owner_metadata' ? !data.require_owner_metadata : !!data.require_owner_metadata,
      route_from_default: key === 'route_from_default' ? !data.route_from_default : !!data.route_from_default
    })
  }

  return jsxs('section', {
    className: 'rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-surface-background) px-3 py-2',
    'aria-label': t('policy.title'),
    children: [
      jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
        jsxs('button', {
          type: 'button',
          className: 'flex items-center gap-1.5 text-sm font-medium text-(--ui-text-primary)',
          'aria-expanded': !collapsed,
          onClick: () => {
            const next = !collapsed
            setCollapsed(next)
            if (storageRef) storageRef.set('policy.collapsed', next)
          },
          children: [
            jsx(Codicon, { name: collapsed ? 'chevron-right' : 'chevron-down', size: '0.9rem' }),
            jsx('span', { children: t('policy.title') }),
            posture && jsx(StatusDot, { tone: postureTone(posture), className: 'ml-1' }),
            posture && jsx('span', { className: 'text-xs font-normal text-(--ui-text-tertiary)', children: t('posture.' + posture) })
          ]
        }),
        jsx('span', { className: 'hidden text-xs text-(--ui-text-quaternary) sm:inline', children: t('policy.desc') })
      ] }),
      save.isError && jsx('p', {
        role: 'alert',
        className: 'mt-1 text-xs text-(--ui-red)',
        children: t('policy.writeFailed') + ': ' + String(save.error && save.error.message ? save.error.message : save.error)
      }),
      !collapsed && jsxs('div', {
        className: 'mt-2 grid gap-x-8 border-t border-(--ui-stroke-quaternary) pt-2 sm:grid-cols-3',
        children: [
          jsx(PolicySwitch, {
            label: t('policy.enabled'),
            checked: !!(data && data.enabled),
            disabled: !data || save.isPending,
            onCheckedChange: () => toggle('enabled')
          }),
          jsx(PolicySwitch, {
            label: t('policy.requireOwner'),
            checked: !!(data && data.require_owner_metadata),
            disabled: !data || save.isPending,
            onCheckedChange: () => toggle('require_owner_metadata')
          }),
          jsx(PolicySwitch, {
            label: t('policy.routeDefault'),
            checked: !!(data && data.route_from_default),
            disabled: !data || save.isPending,
            onCheckedChange: () => toggle('route_from_default')
          })
        ]
      }),
      collapsed && data && save.isPending && jsx(Skeleton, { className: 'mt-2 h-4 w-40' })
    ]
  })
}

// ---------------------------------------------------------------------------
// V1 — ownership map (windowed rows in ScrollArea + search + profile chips)
// ---------------------------------------------------------------------------

// Fixed-height windowed list: listens on the radix scroll viewport that
// contains `ref`; spacer divs pad the unrendered range.
function useViewportScroll(ref) {
  const [view, setView] = useState({ top: 0, height: 800 })
  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    const vp = node.closest('[data-radix-scroll-area-viewport]') || node.parentElement
    if (!vp) return undefined
    function onScroll() {
      setView({ top: vp.scrollTop, height: vp.clientHeight })
    }
    onScroll()
    vp.addEventListener('scroll', onScroll, { passive: true })
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(onScroll) : null
    if (ro) ro.observe(vp)
    return () => {
      vp.removeEventListener('scroll', onScroll)
      if (ro) ro.disconnect()
    }
  }, [ref])
  return view
}

function SkillRow({ row, t, onOpen }) {
  return jsxs('div', {
    role: 'button',
    tabIndex: 0,
    className: 'grid cursor-pointer items-center gap-2 px-3 text-(--ui-text-primary) hover:bg-(--chrome-action-hover) focus-visible:outline outline-(--ui-accent)',
    style: { gridTemplateColumns: GRID, height: ROW_H + 'px' },
    onClick: () => onOpen(row.id),
    onKeyDown: e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onOpen(row.id)
      }
    },
    children: [
      jsx('span', { className: 'truncate text-sm font-medium', title: row.name, children: row.name || row.id }),
      jsx('span', { className: 'truncate text-xs text-(--ui-text-tertiary)', title: row.category, children: row.category || '—' }),
      jsx(ProfileBadge, { name: row.owner }),
      jsxs('span', { className: 'flex min-w-0 items-center gap-1.5', children: [
        row.location.scope === 'global'
          ? jsx(Badge, { variant: 'muted', size: 'xs', children: t('map.filter.global') })
          : jsx(ProfileBadge, { name: row.location.profile }),
        jsx('span', { className: 'truncate text-xs text-(--ui-text-quaternary)', title: row.location.path, children: row.location.path })
      ] }),
      jsx(StateCell, { state: row.state, t: t }),
      jsx('span', { className: 'truncate text-xs text-(--ui-text-quaternary)', children: fmtTime(row.lastAudit) || t('map.never') })
    ]
  })
}

function MapView() {
  const { t } = useI18n()
  const map = useQuery({
    queryKey: [ID, 'map'],
    queryFn: () => apiRest('/map'),
    staleTime: 15_000
  })
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState(() =>
    storageRef ? storageRef.get('filter', 'all') : 'all'
  )
  const [selected, setSelected] = useState(null)
  const viewportRef = useRef(null)
  const view = useViewportScroll(viewportRef)

  const meta = (map.data && map.data.meta) || {}
  const rows = useMemo(
    () => (((map.data && map.data.rows) || []).map(normalizeRow)),
    [map.data]
  )
  const filtered = useMemo(
    () => rows.filter(row => matchesFilter(row, filter) && matchesSearch(row, query)),
    [rows, filter, query]
  )

  const chips = useMemo(() => {
    const list = [{ id: 'all', label: t('map.filter.all') }, { id: 'global', label: t('map.filter.global') }]
    const profiles = Array.isArray(meta.profiles) ? meta.profiles : []
    profiles.forEach(profile => {
      if (profile && profile !== 'global') list.push({ id: profile, label: profile })
    })
    return list
  }, [meta.profiles, t])

  function chooseFilter(next) {
    setFilter(next)
    if (storageRef) storageRef.set('filter', next)
  }

  const detail = useQuery({
    queryKey: [ID, 'map', selected],
    queryFn: () => apiRest('/map/' + encodeURIComponent(selected)),
    enabled: !!selected
  })

  const first = Math.max(0, Math.floor(view.top / ROW_H) - OVERSCAN)
  const last = Math.min(filtered.length, Math.ceil((view.top + view.height) / ROW_H) + OVERSCAN)
  const visible = filtered.slice(first, last)

  return jsxs('div', {
    className: 'flex min-h-0 flex-1 flex-col gap-2',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2',
        children: [
          jsx(SearchField, {
            placeholder: t('map.search'),
            value: query,
            onChange: setQuery,
            'aria-label': t('map.search'),
            containerClassName: 'w-56',
            inputClassName: 'text-(--ui-text-primary)'
          }),
          jsx('div', {
            role: 'group',
            'aria-label': t('nav.label'),
            className: 'flex flex-wrap items-center gap-1',
            children: chips.map(chip =>
              jsx(Button, {
                type: 'button',
                size: 'xs',
                variant: filter === chip.id ? 'secondary' : 'ghost',
                'aria-pressed': filter === chip.id,
                onClick: () => chooseFilter(chip.id),
                children: chip.label
              }, chip.id)
            )
          }),
          map.data && jsx('span', {
            className: 'ml-auto text-xs text-(--ui-text-quaternary)',
            children: filtered.length + ' ' + t('map.count') + ' · ' + t('map.lastAudit') + ': ' + (fmtTime(meta.last_audit_ts) || t('map.never'))
          })
        ]
      }),
      jsx('div', {
        className: 'grid gap-2 px-3 pb-1 text-[0.65rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)',
        style: { gridTemplateColumns: GRID },
        children: ['skill', 'category', 'owner', 'location', 'state', 'audit'].map(h =>
          jsx('span', { key: h, children: h }, h)
        )
      }),
      jsxs(ScrollArea, {
        className: 'min-h-0 flex-1 rounded-lg border border-(--ui-stroke-tertiary)',
        children: [
          map.isLoading && jsx(SkeletonRows, { count: 8 }),
          map.isError && jsx('div', {
            className: 'p-6',
            children: jsx(ErrorState, {
              title: t('error.title'),
              description: String(map.error && map.error.message ? map.error.message : map.error),
              children: jsx(Button, { size: 'sm', onClick: () => map.refetch(), children: t('error.retry') })
            })
          }),
          map.data && filtered.length === 0 && jsx(EmptyState, {
            title: rows.length === 0 ? t('empty.map.title') : t('empty.filter.title'),
            description: rows.length === 0 ? t('empty.map.desc') : t('empty.filter.desc')
          }),
          map.data && filtered.length > 0 && jsxs('div', { ref: viewportRef, children: [
            jsx('div', { style: { height: first * ROW_H + 'px' } }),
            jsx(Separator, { className: 'hidden' }),
            visible.map(row =>
              jsx(SkillRow, { row: row, t: t, onOpen: setSelected }, row.id)
            ),
            jsx('div', { style: { height: (filtered.length - last) * ROW_H + 'px' } })
          ] })
        ]
      }),
      jsx(DetailDrawer, { skillId: selected, detail: detail, onClose: () => setSelected(null) })
    ]
  })
}

// ---------------------------------------------------------------------------
// detail drawer (GET /map/{skill_id})
// ---------------------------------------------------------------------------

function FrontmatterTable({ frontmatter }) {
  const entries = Object.entries(frontmatter || {})
  if (entries.length === 0) return null
  return jsx('dl', {
    className: 'grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-3 gap-y-1',
    children: entries.map(([key, value]) =>
      jsxs('div', { className: 'contents', children: [
        jsx('dt', { className: 'truncate text-xs text-(--ui-text-quaternary)', title: key, children: key }),
        jsx('dd', { className: 'break-words text-xs text-(--ui-text-secondary)', children: typeof value === 'object' ? JSON.stringify(value) : String(value) })
      ] }, key)
    )
  })
}

function DetailDrawer({ skillId, detail, onClose }) {
  const { t } = useI18n()
  if (!skillId) return null
  const data = detail.data || {}
  const rationale = data.rationale || {}
  const history = Array.isArray(data.history) ? data.history : []
  return jsxs(Dialog, {
    open: !!skillId,
    onOpenChange: open => {
      if (!open) onClose()
    },
    children: [
      jsxs(DialogContent, {
        className: 'max-h-[80vh] overflow-y-auto sm:max-w-lg',
        children: [
          jsxs(DialogHeader, { children: [
            jsx(DialogTitle, { children: (data.name || skillId) }),
            jsx(DialogDescription, { children: t('drawer.frontmatter') })
          ] }),
          detail.isLoading && jsx(Skeleton, { className: 'h-24 w-full' }),
          detail.isError && jsx(ErrorState, {
            title: t('error.title'),
            description: String(detail.error && detail.error.message ? detail.error.message : detail.error)
          }),
          detail.data && jsxs('div', {
            className: 'flex flex-col gap-4',
            children: [
              jsx(FrontmatterTable, { frontmatter: data.frontmatter }),
              jsxs('section', { children: [
                jsxs('h3', { className: 'mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-(--ui-text-quaternary)', children: [
                  t('drawer.rationale'),
                  rationale.hoarding_justified === true && jsx(Badge, { variant: 'default', size: 'xs', children: t('drawer.justified') }),
                  rationale.hoarding_justified === false && jsx(Badge, { variant: 'warn', size: 'xs', children: t('drawer.notJustified') })
                ] }),
                jsx('p', {
                  className: 'text-sm text-(--ui-text-secondary)',
                  children: rationale.text || rationale.reason || t('drawer.noRationale')
                })
              ] }),
              jsxs('section', { children: [
                jsx('h3', { className: 'mb-1 text-xs font-semibold uppercase tracking-wide text-(--ui-text-quaternary)', children: t('drawer.history') }),
                history.length === 0
                  ? jsx('p', { className: 'text-sm text-(--ui-text-quaternary)', children: t('drawer.noHistory') })
                  : jsx('ul', {
                      className: 'flex flex-col gap-1',
                      children: history.map((event, index) =>
                        jsxs('li', {
                          className: 'flex items-baseline justify-between gap-3 text-xs',
                          children: [
                            jsx('span', { className: 'min-w-0 break-words text-(--ui-text-secondary)', children: String((event && (event.event || event.action || event.detail)) || JSON.stringify(event)) }),
                            jsx('span', { className: 'shrink-0 text-(--ui-text-quaternary)', children: fmtTime(event && (event.at || event.ts || event.timestamp)) })
                          ]
                        }, index)
                      )
                    })
              ] })
            ]
          }),
          jsxs(DialogFooter, { children: [
            jsx(Button, { variant: 'ghost', onClick: onClose, children: t('drawer.close') })
          ] })
        ]
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// V2 — drift feed (severity chips, canonical findings, confirm-gated resolve)
// ---------------------------------------------------------------------------

function FindingRow({ finding, t, onResolve }) {
  const severity = finding.severity || 'info'
  return jsxs('li', {
    className: 'flex flex-col gap-1.5 rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-surface-background) p-3',
    children: [
      jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
        jsx(Badge, { variant: severityBadgeVariant(severity), size: 'xs', children: t('severity.' + severity) }),
        jsx(Badge, { variant: 'outline', size: 'xs', children: finding.kind || '' }),
        jsx('span', { className: 'text-sm font-medium text-(--ui-text-primary)', children: finding.skill || '' }),
        finding.status === 'open'
          ? jsx(Badge, { variant: 'muted', size: 'xs', children: t('drift.open') })
          : jsx(Badge, { variant: 'default', size: 'xs', children: t('drift.resolved') }),
        jsx('span', { className: 'ml-auto text-xs text-(--ui-text-quaternary)', children: fmtTime(finding.discovered_at) })
      ] }),
      jsxs('div', { className: 'grid gap-x-6 gap-y-0.5 text-xs text-(--ui-text-secondary) sm:grid-cols-2', children: [
        jsxs('span', { children: [t('drift.expected') + ': ', jsx(ProfileBadge, { name: finding.expected_owner })] }),
        jsxs('span', { children: [t('drift.actual') + ': ', jsx('span', { className: 'text-(--ui-text-secondary)', children: finding.actual || '—' })] })
      ] }),
      finding.proposed_fix && jsxs('p', {
        className: 'text-xs text-(--ui-text-tertiary)',
        children: [t('drift.proposed') + ': ', jsx('span', { className: 'text-(--ui-text-secondary)', children: finding.proposed_fix })]
      }),
      finding.status === 'open' && jsx('div', { className: 'flex justify-end', children:
        jsx(Button, { size: 'xs', variant: 'outline', onClick: () => onResolve(finding), children: t('drift.resolve') })
      })
    ]
  })
}

function DriftFeed() {
  const { t } = useI18n()
  const drift = useQuery({
    queryKey: [ID, 'drift'],
    queryFn: () => apiRest('/drift'),
    staleTime: 10_000
  })
  const [target, setTarget] = useState(null)
  const resolve = useMutation({
    mutationFn: finding => apiRest('/drift/' + encodeURIComponent(finding.id) + '/resolve', { method: 'POST', body: {} }),
    onSuccess: invalidateAll
  })
  const findings = ((drift.data && drift.data.findings) || [])
  const meta = (drift.data && drift.data.meta) || {}
  const counts = meta.counts_by_severity || {}

  return jsxs('div', {
    className: 'flex min-h-0 flex-1 flex-col gap-2',
    children: [
      drift.data && jsxs('div', {
        className: 'flex flex-wrap items-center gap-2 text-xs text-(--ui-text-quaternary)',
        children: [
          String(meta.open_count != null ? meta.open_count : findings.length) + ' ' + t('drift.open').toLowerCase(),
          Object.keys(counts).map(sev =>
            counts[sev]
              ? jsxs('span', { className: 'inline-flex items-center gap-1', children: [
                  jsx(StatusDot, { tone: severityTone(sev) }),
                  counts[sev] + ' ' + t('severity.' + sev)
                ] }, sev)
              : null
          )
        ]
      }),
      jsxs(ScrollArea, {
        className: 'min-h-0 flex-1 rounded-lg border border-(--ui-stroke-tertiary)',
        children: [
          drift.isLoading && jsx(SkeletonRows, { count: 5 }),
          drift.isError && jsx('div', {
            className: 'p-6',
            children: jsx(ErrorState, {
              title: t('error.title'),
              description: String(drift.error && drift.error.message ? drift.error.message : drift.error),
              children: jsx(Button, { size: 'sm', onClick: () => drift.refetch(), children: t('error.retry') })
            })
          }),
          drift.data && findings.length === 0 && jsx(EmptyState, {
            title: t('empty.drift.title'),
            description: t('empty.drift.desc')
          }),
          drift.data && findings.length > 0 && jsx('ul', {
            className: 'flex flex-col gap-2 p-3',
            children: findings.map(finding =>
              jsx(FindingRow, { finding: finding, t: t, onResolve: setTarget }, finding.id)
            )
          })
        ]
      }),
      jsx(ConfirmDialog, {
        open: !!target,
        onClose: () => setTarget(null),
        onConfirm: async () => {
          await resolve.mutateAsync(target)
          setTarget(null)
        },
        title: t('drift.confirm.title'),
        description: target
          ? ((target.skill || '') + ' — ' + (target.proposed_fix || target.kind || ''))
          : t('drift.confirm.desc'),
        confirmLabel: t('drift.confirm.ok')
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// page (V1 + V2 + V3 + audit trigger)
// ---------------------------------------------------------------------------

function OwnershipPage() {
  const { t } = useI18n()
  const [tab, setTab] = useState('map')
  const audit = useMutation({ mutationFn: runAuditFlow })

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col gap-3 overflow-hidden p-4',
    children: [
      jsxs('header', {
        className: 'flex flex-wrap items-baseline justify-between gap-2',
        children: [
          jsxs('div', { children: [
            jsx('h1', { className: 'text-lg font-semibold tracking-tight text-(--ui-text-primary)', children: t('page.title') }),
            jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: t('page.subtitle') })
          ] }),
          jsx(Button, {
            size: 'sm',
            variant: 'outline',
            disabled: audit.isPending,
            onClick: () => audit.mutate(),
            children: audit.isPending ? t('audit.running') : t('audit.run')
          })
        ]
      }),
      jsx(PolicyPanel, {}),
      jsx(SegmentedControl, {
        value: tab,
        onChange: setTab,
        options: [
          { id: 'map', label: t('tabs.map') },
          { id: 'drift', label: t('tabs.drift') }
        ]
      }),
      tab === 'drift' ? jsx(DriftFeed, {}) : jsx(MapView, {})
    ]
  })
}

// ---------------------------------------------------------------------------
// V4 — statusbar chip (worst severity; socket accel + poll fallback)
// ---------------------------------------------------------------------------

function useSummary() {
  return useQuery({
    queryKey: [ID, 'summary'],
    queryFn: () => apiRest('/drift/summary'),
    refetchInterval: SUMMARY_POLL_MS, // socket is a no-op on OAuth remotes — keep the poll
    staleTime: 5_000
  })
}

function StatusChip() {
  const { t } = useI18n()
  const { data } = useSummary()
  if (!data || !Number(data.open_count)) return null
  const worst = data.worst_severity
  const label =
    String(data.open_count) + ' ' + t('status.tooltip') +
    (worst ? ' — ' + t('severity.' + worst) : '')
  return jsx(Tip, {
    label: label,
    children: jsxs('button', {
      type: 'button',
      className: cn(
        'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] tabular-nums transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-(--ui-text-primary)'
      ),
      onClick: () => host.navigate('/skill-ownership'),
      'aria-label': label,
      children: [
        jsx(Codicon, { name: 'pulse', size: '0.7rem' }),
        jsx(StatusDot, { tone: severityTone(worst) }),
        jsx('span', { children: String(data.open_count) })
      ]
    })
  })
}

// ---------------------------------------------------------------------------
// registration
// ---------------------------------------------------------------------------

const plugin = {
  id: ID,
  name: 'Skill Ownership',
  description: 'Ownership map, drift feed, and routing policy for fleet skill owner routing.',
  defaultEnabled: false,
  register(ctx) {
    restRef = ctx.rest
    storageRef = ctx.storage
    i18nTRef = ctx.i18n && ctx.i18n.t
    if (ctx.i18n && ctx.i18n.register) ctx.i18n.register(translations)
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/skill-ownership' },
        render: () => jsx(OwnershipPage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { codicon: 'organization', label: tStatic('nav.label'), path: '/skill-ownership' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: ID + '.open',
          label: tStatic('palette.open'),
          keywords: ['skill', 'ownership', 'drift', 'map'],
          run: () => host.navigate('/skill-ownership')
        }
      },
      {
        id: 'audit',
        area: PALETTE_AREA,
        data: {
          id: ID + '.audit',
          label: tStatic('palette.audit'),
          keywords: ['skill', 'audit', 'drift', 'scan'],
          run: () => {
            host.navigate('/skill-ownership')
            void runAuditFlow()
          }
        }
      },
      {
        id: 'policy',
        area: PALETTE_AREA,
        data: {
          id: ID + '.policy',
          label: tStatic('palette.policy'),
          keywords: ['skill', 'policy', 'routing', 'owner'],
          run: () => {
            host.navigate('/skill-ownership')
            $expandPolicy.set(true)
          }
        }
      },
      {
        id: 'statusbar',
        area: STATUSBAR_AREAS.right,
        order: 120,
        render: () => jsx(StatusChip, {})
      }
    ])
    // Live drift push (no-op on OAuth remotes — the poll above is the floor).
    if (ctx.socket) {
      ctx.socket('/events', () => {
        invalidateAll()
      })
    }
    ctx.onDispose(() => {
      restRef = null
      storageRef = null
      i18nTRef = null
    })
  }
}

export {
  auditOutcome,
  matchesFilter,
  matchesSearch,
  nestMessages,
  normalizeRow,
  plugin as default,
  plugin,
  pollRun,
  postureOf,
  postureTone,
  runAuditFlow,
  severityBadgeVariant,
  severityTone,
  stateTone,
  translationTable,
  translations
}
