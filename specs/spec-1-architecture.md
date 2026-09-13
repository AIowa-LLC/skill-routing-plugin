# SPEC-1 — Plugin Architecture & Enforcement Engine
Owner profile: `developer` | Status: v1.0 (crew-reviewed, amendments merged 2026-08-24) | Spec-only — no build authorized yet

## Objective
Define the architecture for `skill-owner-routing`, a standalone Hermes plugin that
enforces fleet skill ownership: creation routing, sideways-write denial, maintenance
drift-watchdog, and the No-Specialist-Hoarding discipline — without modifying core.

## Ground truth (verified 2026-08-24, do not re-derive)
- Upstream PR #87101 `feat(skills): route learned skills by owner profile` — OPEN,
  head `tony/skill-owner-routing@29dc92a4`, base main, 1 commit / 5 files. Not merged.
- Local commit `e12d79edd1` preserves the exact logic:
  `_skill_owner_routing_policy()`, `_declared_skill_owner()` (reads
  `metadata.hermes.owner_profile`), `_create_skill_with_owner_routing()`
  (tools/skill_manager_tool.py +304/-50), config `skills.owner_routing`
  (config_defaults.py +9), tests +318.
- Routed-create mechanism uses public primitives only:
  `set_hermes_home_override(get_profile_dir(owner))` + `atomic_write_text`,
  owner validated via `validate_profile_name` BEFORE any FS lookup, transaction
  scoped to target home (ledger + usage consistency).
- The maintenance watchdog (`skill_owner_issues()`) is GONE from source and from
  any PR — must be rebuilt from scratch in this plugin.
- Hook surface: `pre_tool_call` returns approve/deny/escalate (tools/approval.py
  :3636/:4008). Precedent: hardproof enforces policy via these hooks, zero core edits.
- Corollary PR #87138 (cron specialist delivery broker) — out of scope here, noted.

## Package layout (unified folder, installs/uninstalls as one)
```
~/.hermes/plugins/skill-owner-routing/
├── plugin.yaml            # kind: standalone; provides_hooks: [pre_tool_call];
│                          #   provides_tools: [skill_owner_create, skill_owner_audit]
├── skill_owner_routing/   # python package
│   ├── policy.py          #   policy read (DEFAULT profile config — fleet rule)
│   ├── frontmatter.py     #   owner_profile parse/validate
│   ├── gate.py            #   pre_tool_call decision engine (early-bail for non-skill_manage)
│   ├── routed_create.py   #   ported routed-create transaction (from e12d79edd1)
│   ├── drift.py           #   REBUILT watchdog: ownership drift scan — emits
│                          #   findings in the canonical record shape defined
│                          #   in SPEC-0 Interface 3 ({id, kind, severity,
│                          #   skill, expected_owner, actual, proposed_fix,
│                          #   discovered_at, status}; kind per that enum).
│   ├── hoarding.py        #   No-Specialist-Hoarding policy lint
├── dashboard/plugin_api.py  # FastAPI router (see SPEC-2 contract)
├── desktop/plugin.js        # UI half (SPEC-2)
└── tests/                   # unit + E2E w/ temp HERMES_HOME (SPEC-3)
```

## Enforcement surface (Tony ruling: FULL coverage)
1. **create** — `pre_tool_call` on `skill_manage{action:create}`:
   - Parse frontmatter → `owner_profile`.
   - Missing owner + `require_owner_metadata` → DENY with guidance (same message
     contract as PR #87101).
   - `active == owner` → APPROVE (vanilla path).
   - `active == default` + `route_from_default` → DENY plain create, instruct
     `skill_owner_create` (plugin tool) which performs the routed transaction:
     write into owner profile home, scoped override, ledger+usage preserved.
   - Named specialist + sideways target → DENY: "Hand the skill creation to the
     `<owner>` profile."
   *Design note (reviewed): the pre_tool_call dispatcher does support an
     args-rewriting `modify` directive (hermes_cli/plugins.py:6188-6199), but
     `skill_manage`'s parameter surface has no home/profile/path argument to
     rewrite — the redirect target lives in _create_skill's FS resolution,
     unreachable from args. Deny-redirect to skill_owner_create is therefore
     the correct and only clean routed-create path. RULING CONFIRMED.*
2. **edit / patch / delete / write_file / remove_file / archive** — same
   ownership rule on the target skill's current location: sideways mutations
   denied; owner or default allowed per policy.
   *Design notes (reviewed):* ONE hook registration on `pre_tool_call`
   (gate.py), early-bail on `tool_name != "skill_manage"`, action-switched
   over the full mutation set — all of them mutate a target skill's current
   location. Target resolution needs I/O: maintain a name→home index built
   by the drift scan, validated per decision by `Path.exists()` on the
   indexed SKILL.md, bounded scan fallback on miss (profile count is small).
   The plugin fires at dispatch — BEFORE the tool body — so plugin-block wins
   and no double-staging with core's `_apply_skill_write_gate` approval
   occurs. Return `block` (deterministic deny, PR #87101 message contract);
   do NOT use `approve`-escalate for sideways writes — a human `[a]lways`
   on the approval gate would permanently weaken a fleet rule.
3. **drift watchdog** (REBUILT — no upstream basis):
   - scan all profiles + global: owner_profile vs actual location; unknown owner
     IDs; path/metadata drift; global+profile duplicates (hoarding signal).
   - scheduled + on-demand (`skill_owner_audit`); findings to ledger + dashboard;
     PROPOSES fixes, never auto-mutates (approval-gated per fleet rules).
4. **hoarding lint** (Tony ruling: plugin owns the discipline):
   - global-scope skills must justify: control-plane / shared-primitive /
     verified-structural-dependency. Unjustified → drift finding.

## Config contract
- Same key as core: `skills.owner_routing` in the **DEFAULT profile** config
  (a specialist must not weaken the fleet rule).
- `{enabled, require_owner_metadata, route_from_default}`.
- Default-posture reconciliation (Tony: opt-in install, default-ON, user-disable):
  plugin treats **key absent = enabled** (installing the plugin is the opt-in);
  explicit `enabled: false` disables. Core PR treats absent = disabled. Document
  the divergence in README; both honor an explicit value identically.

## Core coexistence idempotency (critical)
Feature-detect per create-decision (mtime-cached config read): (1) core symbol
present — `getattr(tools.skill_manager_tool, "_skill_owner_routing_policy",
None) is not None` (exact symbol PR #87101 introduces); (2) core policy
`enabled` resolves true from DEFAULT-home config. Both true → the CREATE gate
goes DORMANT (log once, audit-only); drift watchdog, hoarding lint, and UI
stay active unconditionally. Dormancy is scoped to the create gate ONLY —
PR #87101 gates create exclusively (verified: edit/patch/delete/write_file/
remove_file dispatch unchanged), so sideways-mutation gating remains
plugin-owned regardless of core merge state. Never double-deny.

AMENDMENT (2026-09-13, card t_5c92790e): the dormancy clause above is
conditional on core support that no longer exists. PR #87101 was briefly
merged (observed at core `809e94ca4c`, 2026-08-24) and has since been
removed — current core (v0.21.2 @ `3e09e5a15f`, verified 2026-09-13)
carries neither the probe symbol nor `skills.owner_routing` defaults.
On any symbol-absent core the create gate is ACTIVE BY DESIGN: fully
active on (re)install is the accepted posture (Tony ruling 2026-09-12 —
single-user fleet, the gate is the product). Dormancy remains implemented
and correct for any future core that reintroduces enforcement.

## Watchdog scheduling (reviewed)
Hermes cron on the DEFAULT profile, `no_agent: true` script mode — the
scheduler runs the drift scan entrypoint directly (zero LLM cost, empty
stdout = silent, non-empty = findings digest delivered; classic watchdog
pattern). Plugin-side timers rejected: `register()` runs per agent session →
thread dies with session, duplicates across concurrent sessions, no
persistence; hardproof ships no background timer and there is no
plugin-background-task facility. Kit installer registers the job in the
default-profile cron registry; plugin README documents `hermes cron` removal.
Cadence: daily 04:00 + on-demand via `skill_owner_audit`. Findings → plugin
ledger file in DEFAULT home (fleet-level surface) + dashboard (SPEC-2).
Note: gateway ticker only ticks default unless `gateway.multiplex_profiles:
true` (verified constraint).

## Non-negotiables
No core file edits. Profile-safe paths (`get_hermes_home`). Atomic writes.
No secrets in code/logs. Decision path <50ms with early bail on non-skill_manage
calls (no I/O). All handlers return JSON strings. MIT + license headers.

## Acceptance for this spec
Developer reviews feasibility of: deny-redirect vs. any cleaner routed-create
path; the core-detection probe; watchdog scheduling (plugin-side timer vs cron).
Output: finalized SPEC-1 v1.0 + any amendments, NOT code.

## References
- Workspace: /home/tony/projects/skill-routing-plugin/
- Local commit: e12d79edd1 (branch tony/skill-owner-routing)
- PR: https://github.com/NousResearch/hermes-agent/pull/87101
- Precedent plugin: ~/.hermes/plugins/hardproof/ (hooks pattern)
