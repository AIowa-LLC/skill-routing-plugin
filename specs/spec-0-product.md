# SPEC-0 — Product Contract & Fleet Integration
Owner profile: `default` (Orchestrator) | Status: v1.0 (crew-reviewed, amendments merged 2026-08-24) | Spec-only

## Objective
Bind the specs together: product contract, fleet integration rules, sequencing,
and the human gates. This is the integrating document the crew works from.

## Product definition (Tony's rulings, locked 2026-08-24)
- **What**: `skill-owner-routing` — standalone Hermes plugin enforcing fleet
  skill ownership: creation routing + sideways denial + maintenance drift
  watchdog + No-Specialist-Hoarding discipline.
- **Coverage**: FULL — skill management, routing, and maintenance.
- **Posture**: opt-in INSTALL (nothing happens until user installs/toggles),
  default-ON once installed, explicit user-disable via `skills.owner_routing.enabled: false`.
- **Scope**: plugin owns the No-Specialist-Hoarding discipline (SOUL-aligned),
  not just mechanical write-routing.
- **Distribution**: eventually shipped in Hermes Desktop Kit
  (github.com/asimons81/hermes-desktop-kit) — public-facing from day one.

## The four-lane spec set
| Spec | Owner | Covers |
|---|---|---|
| SPEC-0 (this) | orchestrator | product contract, sequencing, gates |
| SPEC-1 | developer | architecture + enforcement engine + coexistence |
| SPEC-2 | frontend | dashboard UI, REST contract, kit-readiness |
| SPEC-3 | qa | acceptance matrix, release gates, evidence standard |

## Interfaces between specs (integration contract)
1. **Policy truth**: single source = `skills.owner_routing` in DEFAULT profile
   config. SPEC-1 reads it, SPEC-2 renders/writes it, SPEC-3 tests writes.
2. **Backend API**: SPEC-1's `dashboard/plugin_api.py` implements the routes
   SPEC-2 defines (map/drift/policy/audit/resolve). Any contract change requires
   both owners' sign-off — logged on the Kanban cards.
3. **Findings model**: drift findings shape shared by watchdog (SPEC-1) and
   Drift Feed (SPEC-2): {id, kind, severity, skill, expected_owner, actual,
   proposed_fix, discovered_at, status}. QA evidence references these fields.
   The canonical shape is BINDING on all consumers: `kind` is restricted to
   {drifted, misplaced-global, unknown-owner, duplicate/hoarding, unowned}.
   SPEC-1 drift finding records, SPEC-2 Drift Feed rows/resolve payloads, and
   SPEC-3 Gate B evidence all serialize THIS schema; no spec defines a
   divergent shape.
4. **Dormancy**: core-coexistence behavior is a SPEC-1 mechanism with SPEC-2
   surface (posture display) and SPEC-3 Gate A7b/D5 coverage.

## Non-goals
- No upstream contribution work in this project (PR #87101 continues its own
  life; we do not block on it, and we do not withdraw it).
- No cron-delivery brokering (PR #87138's lane) — plugin may surface drift in
  cron-owned skills but does not touch cron routing.
- No automated replies / messaging surfaces.
- No changes to Tony's live SOUL discipline until plugin ships; the plugin
  encodes the rule, SOUL remains the human-authored authority.

## Sequencing (spec phase now; build phase gated)
1. **Spec review (now)** — developer, frontend, qa review their specs; amendments
   logged as card comments; orchestrator merges → v1.0 set.
2. **Build phase gate** — Tony approves build start (human gate #1).
3. Build: engine (SPEC-1) → backend API → UI (SPEC-2) in parallel tracks once
   REST contract frozen; QA builds test harness against SPEC-3 matrix in parallel.
4. **Release gate** — Gate D PASS required before kit release (human gate #2,
  Tony's approval for the public release itself).

## Fleet integration rules
- Fleet-wide policy read: plugin on the GEEKOM (fleet commander) reads DEFAULT
  config; TONY-GAMING-TOP is a compute peer — plugin data there stays local
  (no cross-host authority changes; A2A stays transport).
- Watchdog scheduling: plugin-local timer (no new recurring cron without
  orchestrator review — see Cron Ownership rules).
- No raw secrets in findings, logs, or dashboard payloads.

## Acceptance for this spec
Orchestrator self-review complete; the spec set is internally consistent. Tony
reviews the v1.0 set (or amendments land first) → clears build gate.

## References
- Discovery card: kanban board `skill-routing-plugin`, card t_611b1103
- Ground-truth commit: e12d79edd1; PR #87101; corollary PR #87138
- Workspace: /home/tony/projects/skill-routing-plugin/specs/
