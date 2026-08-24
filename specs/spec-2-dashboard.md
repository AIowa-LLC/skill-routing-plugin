# SPEC-2 — Dashboard & Desktop Kit Surface
Owner profile: `frontend` | Status: v1.0 (crew-reviewed, amendments merged 2026-08-24) | Spec-only — no build authorized yet

## Objective
Define the user-facing surface of `skill-owner-routing` so the plugin ships
dashboard-native and Hermes-Desktop-Kit-ready from day one. "Sexy but native":
looks built-in, zero hardcoded colors, survives hot reload.

## Verified UI surface (from hermes-agent skill, desktop-plugins.md reference)
- Unified package door: `~/.hermes/plugins/<id>/desktop/plugin.js` — desktop half
  of the unified plugin folder; OPT-IN (Settings → Plugins toggle). One folder
  installs/uninstalls everything (Python + dashboard API + UI).
- SDK: import ONLY `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`. `jsx()` calls,
  never JSX syntax. `ctx.register({ id, area, ... })`; `host.request` for gateway
  RPC; `ctx.rest('/path')` + `ctx.socket('/events')` scoped to `/api/plugins/<id>`;
  `ctx.storage` namespaced persistence; `useQuery`/`useMutation` + queryClient.
- Native component kit: Button, Switch, Tabs*, Badge, StatusDot, ScrollArea,
  Separator, Skeleton, GlyphSpinner, EmptyState, ErrorState, CopyButton, SearchField,
  SegmentedControl, Dialog*, Tooltip*, Codicon. Style ONLY with theme vars
  (`var(--ui-accent)`, `var(--ui-text-secondary)`...). NEVER hardcode colors.
- Routes: `area: ROUTES_AREA` + `SIDEBAR_NAV_AREA` row (below Artifacts) + palette
  command. Panes: `area: 'panes'` with placement/dock hints.

## Views
### V1 — Ownership Map (main view, `area: ROUTES_AREA`, path `/skill-ownership`)
Sidebar nav: label "Skill Ownership", codicon `organization` or `repo-forked`.
One row per fleet skill (windowed rendering inside ScrollArea; SearchField + profile filter chips):
| Field | Source |
|---|---|
| name + category chip | SKILL.md frontmatter |
| owner profile badge | `metadata.hermes.owner_profile` |
| actual location | scanned path (global vs profile) |
| drift state | clean / drifted / unowned / unknown-owner / duplicate |
| last audit | drift scan timestamp |

Row states: `clean` (StatusDot green), `drifted` (amber), `unowned` (gray) —
new skills lacking owner metadata, `unknown-owner` (amber, hollow) — owner_profile
names a profile that is not registered, `duplicate` (red) — global+profile copies.
Click → detail drawer: frontmatter summary, ownership rationale (hoarding-justified
or not), route history, actions.

### V2 — Drift Feed
Time-ordered findings from the watchdog: what drifted, when, severity, proposed
fix (e.g. "move to trt, archive global copy"), resolve action → mutation to
`plugin_api.py` (approval-gated, confirm dialog via ConfirmDialog).
Drift Feed rows and the resolve mutation serialize the canonical findings
record from SPEC-0 Interface 3 (fields as defined there; kind enum values
render directly).

### V3 — Policy panel (collapsible card pinned at top of /skill-ownership)
Enabled switch + `require_owner_metadata` + `route_from_default` switches.
Writes via plugin REST → core config `skills.owner_routing` (DEFAULT profile
config). Shows effective posture: "Default-ON (plugin installed) / User-disabled /
Core-managed (dormant)" with StatusDot + one-line explanation.

### V4 — Statusbar chip (area: 'statusBar.right')
`[icon] N` where N = open drift findings; StatusDot colored by worst severity;
tooltip = summary; click → navigate('/skill-ownership'). Data via `ctx.socket`
('/events') with `refetchInterval` polling fallback (socket is no-op on OAuth
remotes — MUST keep the fallback).

### V5 — Palette commands (PALETTE_AREA)
- "Skill Ownership: Open map" → navigate
- "Skill Ownership: Run audit now" → useMutation → toast result
- "Skill Ownership: Toggle policy" → opens V3 panel

## REST/WS contract (backend = SPEC-1's dashboard/plugin_api.py)
```
GET  /api/plugins/skill-owner-routing/map               → V1 rows[] + meta{profiles[], last_audit_ts}
GET  /api/plugins/skill-owner-routing/map/{skill_id}    → detail drawer: frontmatter, rationale, history[]
GET  /api/plugins/skill-owner-routing/drift             → V2 findings[] + meta{open_count, counts_by_severity}
GET  /api/plugins/skill-owner-routing/drift/summary     → V4 chip {open_count, worst_severity} — cheap frequent poll
POST /api/plugins/skill-owner-routing/audit/run         → 202 {run_id}
GET  /api/plugins/skill-owner-routing/audit/runs/{run_id} → {state: running|done|failed, findings_count} — poll fallback for the 202
GET  /api/plugins/skill-owner-routing/policy            → {enabled, require_owner_metadata, route_from_default,
                                                          key_present, core_enforces, posture}
PUT  /api/plugins/skill-owner-routing/policy            → gated write; returns resulting policy + config diff
POST /api/plugins/skill-owner-routing/drift/{id}/resolve → confirm-gated; returns the resolved finding
```
Error shape: FastAPI default + `detail` human message; UI renders ErrorState with
retry. All mutations return the resulting state for optimistic-rollback clarity.

## Design language rules
- Theme vars only. Blank-state = EmptyState ("No skills mapped yet — the audit
  scan populates this once installed"). Loading = Skeleton rows, not spinners-in-void.
- Drift severity colors: reuse theme semantic vars; never custom hex.
- Badge profile colors: derive from a fixed palette of theme-compatible vars,
  hash(profile) → index. No custom branding colors.
- i18n: author flat dot-keys, expand to NESTED trees before `ctx.i18n.register`
  (the SDK resolver walks nested objects; flat registration renders raw keys).
  `usePluginI18n(id)` IS the t function — never destructure `{ t }` from it.
- Live brand note: personal Obsidian & Crimson aesthetic is NOT imported here —
  this is a shipped public plugin; theme-native is the brand.
- Severity enum: `info | warning | critical` on every finding; StatusDot and
  chip color derive from theme semantic vars keyed by severity.

## Desktop Kit readiness
- Kit subset: `desktop/ + dashboard/ + tests/ + docs md` copy verbatim into
  `asimons81/hermes-desktop-kit` as `skill-owner-routing/`. The python engine
  (`skill_owner_routing/` + plugin.yaml) stays in the unified install folder;
  kit README links the engine install. (Kit precedent folders ship no
  plugin.yaml — spotify-desktop shape: dashboard/manifest.json, desktop/,
  tests/, scripts/, *.md.)
- `defaultEnabled: false` on the desktop half per SDK opt-in rule. Gate layers are distinct: the Python/enforcement half is default-ON once the user enables the plugin via `plugins.enabled` (config key absent = ENABLED, per SPEC-1); the desktop UI half is separately OFF until the in-app toggle is flipped. "Off until user acts" applies ONLY to the desktop UI toggle. This matches Tony's opt-in-install (enable the plugin) + default-ON read (post-install policy).
- README with toggle instructions ("Settings → Plugins → flip it on") + screenshot.

## Acceptance for this spec
Frontend reviews: information architecture (is the map the right main view?),
REST contract fit (any missing endpoint), kit-ready constraints. Output: SPEC-2
v1.0 + amendments, NOT code.

## References
- SDK reference: hermes-agent skill → references/desktop-plugins.md
- Precedent: ~/.hermes/plugins/github-pr-dashboard/ (FastAPI backend + manifest)
- Kit repo: github.com/asimons81/hermes-desktop-kit
