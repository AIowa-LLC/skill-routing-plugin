# SPEC-3 — Verification & Release-Gate Design
Owner profile: `qa` | Status: v1.0 (crew-reviewed, amendments merged 2026-08-24) | Spec-only — no build authorized yet

## Objective
Define the independent acceptance design for the `skill-owner-routing` plugin:
what "done" provably means, which gates run before any release, and the evidence
each gate must produce. QA owns verdicts (PASS/HOLD/BLOCKED), not repairs.

## Scope of verification
1. Enforcement engine (SPEC-1): create routing, sideways denial, edit/delete/
   archive gating, config contract, core-coexistence dormancy.
2. Dashboard surface (SPEC-2): rendered views, REST contract, kit-readiness.
3. Release readiness: packaging, permissions, security, no-regression on core.

## Gate A — Enforcement semantics (from PR #87101 test matrix, extended)
| # | Scenario | Expected |
|---|---|---|
| A1 | default + create, owner=trt | routed create under trt; ledger+usage intact |
| A2 | trt + create, owner=trt (own) | plain create under trt |
| A3 | growth + create, owner=trt (sideways) | DENY + "Hand the skill creation to the 'trt' profile." |
| A3b | growth + edit/delete/archive on trt-owned skill | DENY (sideways mutation) |
| A3c | default + edit/delete/archive on trt-owned | allowed (owner or default may maintain; no config knob exists) |
| A3d | specialist + edit/delete/archive on OWN skill | allowed |
| A4 | any + create, no owner metadata, require=true | DENY + guidance message |
| A5 | create, owner=nonexistent profile | DENY + "not registered" |
| A6 | owner=<invalid chars> | DENY before FS lookup (validate_profile_name) |
| A6b | owner=<path-traversal / yaml-injection payload> | DENY at validation; payload never reaches a path join or FS write |
| A7 | policy read from DEFAULT config | specialist weakening own config does NOT weaken rule |
| A7b | core enforces (simulated) | plugin create-gate DORMANT (audit-only, no deny; dormancy logged once, not per-call) |
| A8 | `enabled: false` explicit | full bypass incl. drifted legacy skills |
| A8b | key absent + plugin installed | ENABLED (default-ON posture) |
| A8c | `route_from_default: false` + default create, owner=trt | DENY + "configured to refuse cross-profile creation; delegate the create." (amended 2026-08-24 t_4f4ff38c: matches upstream e12d79edd1 refusal semantics — explicit refuse denies the create entirely; nothing lands locally and nothing routes) |
| A8d | `require_owner_metadata: false` + create w/o owner | allowed; unowned skill created (surfaces as V1 `unowned` row state) |
| A8e | default + create, owner=default | plain create under default (owner==default is not a route) |
| A9 | decision latency | p95 <50ms over N≥1000 non-skill_manage pre_tool_call calls; early-bail does zero filesystem/config I/O (assert no open/read, not just timing) |
| A9b | routed-create round trip | write lands under target profile home, ledger+usage consistent |

Message-contract assertions pin stable upstream substrings, not full strings:
"may not write skills sideways" (A3), "is not registered" (A5),
"owner_profile" (A4), "Hand the skill creation to" (A3/A9b deny-redirect),
"refuse cross-profile creation" (A8c refusal deny).

## Gate B — Drift watchdog (rebuilt, no upstream basis)
| # | Scenario | Expected |
|---|---|---|
| B1 | skill in wrong profile vs owner_profile | finding: drifted |
| B1b | global skill w/ owner_profile set (hoarding signal) | finding: misplaced-global |
| B1c | global-scope skill, no owner_profile, no justification | finding: unowned (hoarding lint, SPEC-1 §4; label amended 2026-08-24 t_4f4ff38c — SPEC-0 Interface 3 enum is binding and has no `unjustified-global` kind) |
| B1d | global-scope skill w/ valid justification (control-plane / shared-primitive / verified-structural-dependency) | clean — no finding |
| B2 | owner id not a registered profile | finding: unknown-owner |
| B2b | global copy + profile copy both exist | finding: duplicate/hoarding |
| B3 | scan on N=500 skills (generated fixture fleet) | completes <10s, no partial state |
| B3b | concurrent scan + create | no race, no lost findings |
| B4 | propose-fix path | proposes, never auto-mutates (approval-gated) |
| B4b | audit run via REST/CLI | 202 + run_id; result lands in feed; idempotent re-run |

## Gate C — Dashboard acceptance (rendered evidence, not code review)
- C1: Ownership Map renders rows from /map with all row states visible
  (clean/drifted/unowned/unknown-owner/duplicate).
- C2: Drift Feed ordering, severity chips, resolve flow with ConfirmDialog.
- C3: Policy panel writes effective policy (visible in config file after PUT;
  returned diff rendered).
- C4: Statusbar chip reflects worst open severity; navigates on click.
- C5: Hot reload: save plugin.js → reloads without error toast; state survives
  (proof: applied filter/search still active post-reload).
- C5b: No hardcoded colors: grep comment-stripped plugin.js for `#`-hex /
  rgb( — zero hits; any hit is manually adjudicated before FAIL.
- C5c: Only allowed imports (@hermes/plugin-sdk, react, react/jsx-runtime).
- C6: OAuth-remote session: socket no-op → polling fallback keeps data fresh
  (socket transport may be mocked; app code under test is real).
- C7: Empty/loading/error states use EmptyState/Skeleton/ErrorState components.
- C8: Palette commands: "Open map" navigates, "Run audit now" fires mutation +
  toast, "Toggle policy" opens the V3 panel.
Rendered-capture standard: each row records pre-state, action, post-state
capture (screenshot/DOM extract), and console-log extract; any error toast or
console error fails the row.

## Gate D — Release readiness (QA-L3; plugin is public-facing — adversarial + recovery mandatory)
- D1: Kit packaging: kit subset (desktop/ + dashboard/ + tests/ + docs md) copies
  verbatim into hermes-desktop-kit; no absolute paths; no Tony-specific references.
- D2: Permissions: no secrets read/written; no network beyond localhost backend;
  config writes limited to `skills.owner_routing` subtree. Method: static grep
  for network primitives (requests/httpx/urllib/socket) in the python package;
  plugin.js restricted to host.request/ctx.rest/ctx.socket.
- D3: Determinism: two audits of same fleet state → identical findings.
  Compare sorted, excluding run_id and discovered_at timestamps.
- D4: Uninstall cleanliness: remove folder → no orphan state (ctx.storage
  namespaced, ledger entries self-contained).
- D4b: Install on fresh profile: absent config key → default-ON works day one.
- D4c: After uninstall, leftover `skills.owner_routing` config key is inert —
  no hooks registered, creates behave vanilla.
- D5: Core upgrade path: simulate core merge of #87101 → plugin detects, goes
  dormant on create-gate, keeps watchdog+UI. No double-deny. Provenance probe:
  while core enforces, a create attempt yields exactly ONE denial and it
  originates from core (message provenance check), plugin audit-logs only.
- D5b: Simulated partial core (policy exists, routing absent) → fail-safe
  decision documented and tested.
- D5c: Core enforcement removed after dormancy → plugin gate REACTIVATES on
  next decision (no zombie dormancy cache); watchdog never went dormant.
- D5 is the highest-risk scenario; test with real core branch, not mocks.

**Gate-D verdict required before any public kit release.**

## Evidence & process
- Every gate row maps to at least one automated test (pytest, temp HERMES_HOME,
  real imports — mocks only for gateway/websocket externals) or a scripted
  rendered-evidence capture for UI rows.
- Matrix lives with the repo as `qa/matrix.md`; results appended per run with
  environment fingerprint: hermes-agent core commit, plugin commit, OS, Python
  version — plus the core test-branch SHA for A7b/D5 rows.
- Candidate identity (repo, commit, dirty state) is pinned before every gate
  run; each result row names the commit it certifies.
- A HOLD routes work back to the implementation owner (developer/frontend) for
  repair, then returns to QA for re-verification. QA never repairs the candidate.
- Rerun policy (tiered by changed surface):
  - enforcement code (gate.py, routed_create.py, policy.py, frontmatter.py) →
    Gates A+D in full;
  - watchdog (drift.py, hoarding.py) → Gate B + B3 perf re-check;
  - UI only (plugin.js) → Gate C;
  - REST contract change → B4b + C3, plus both owners' sign-off per SPEC-0
    §Interfaces;
  - dormancy probe change → A7b + D5 cluster;
  - spec/matrix amendment → re-baseline affected rows before the next run.

## Acceptance for this spec
QA reviews: matrix completeness (missing scenarios? ordering? dormancy
fail-safe definition), evidence standard (is rendered capture acceptable for C?),
release-gate scope (should D be L3 given public kit release?). Output: SPEC-3
v1.0 + amendments, NOT test code.

## References
- Workspace: /home/tony/projects/skill-routing-plugin/specs/
- PR test matrix base: local commit e12d79edd1 tests (+318 lines)
- Fleet QA standard: SOUL routing table (QA owns evidence-backed verdicts)
