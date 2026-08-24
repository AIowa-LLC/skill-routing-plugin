# QA Matrix — skill-owner-routing (SPEC-3 §Evidence & process)

Results are APPENDED per run. Each run pins candidate identity (repo, commit,
dirty state, environment) per SPEC-3. Verdicts: PASS / FAIL / NOT_TESTED.
Row maps: `tests/qa/test_gate_a.py::test_<row>` and `tests/qa/test_gate_b.py::test_<row>`.

## Gate A — enforcement semantics

| # | Scenario | Expected | Test |
|---|----------|----------|------|
| A1 | default + create, owner=trt | routed create under trt; ledger+usage intact | test_A1_default_create_owner_trt_redirects_to_routed_create |
| A2 | trt + create, owner=trt | plain create under trt | test_A2_owner_equals_active_plain_create |
| A3 | growth + create, owner=trt | DENY + sideways + handoff message | test_A3_named_profile_cannot_write_sideways |
| A3b | growth + edit/delete/archive on trt skill | DENY | test_A3b_sideways_mutation_denied[{edit,delete,archive}] |
| A3c | default + edit/delete/archive on trt skill | allowed | test_A3c_default_may_maintain_specialist_skill |
| A3d | specialist maintains OWN skill | allowed | test_A3d_owner_may_maintain_own_skill |
| A4 | create, no owner, require=true | DENY + guidance | test_A4_missing_owner_metadata_denied |
| A5 | create, owner=nonexistent | DENY + "not registered" | test_A5_unknown_owner_denied |
| A6 | owner=invalid chars | DENY before FS lookup | test_A6_invalid_owner_denied_at_validation[{../../tmp,root,bad/profile}] |
| A6b | traversal/yaml-injection payload | DENY at validation; FS never touched | test_A6b_injection_payloads_never_reach_fs |
| A7 | policy read from DEFAULT config | specialist weakening ignored | test_A7_policy_read_from_default_home |
| A7b | core enforces (simulated) | DORMANT, logged once | test_A7b_dormant_when_core_enforces |
| A8 | enabled: false | full bypass incl. drifted legacy | test_A8_explicit_disable_is_full_bypass |
| A8b | key absent | ENABLED | test_A8b_key_absent_means_enabled |
| A8c | route_from_default: false | no deny, no route | test_A8c_route_from_default_false_stays_on_default |
| A8d | require_owner_metadata: false | unowned create allowed | test_A8d_optional_metadata_allows_unowned_create |
| A8e | default + create, owner=default | plain create | test_A8e_owner_default_is_not_a_route |
| A9 | decision latency | p95 <50ms N≥1000, zero-I/O early-bail | test_A9_latency_p95_and_zero_io_early_bail |
| A9b | routed-create round trip | lands in owner home, ledger+usage | test_A9b_routed_create_round_trip |

## Gate B — drift watchdog

| # | Scenario | Expected | Test |
|---|----------|----------|------|
| B1 | wrong profile vs owner_profile | drifted | test_B1_wrong_profile_is_drifted |
| B1b | global w/ owner set | misplaced-global | test_B1b_global_with_owner_is_misplaced_global |
| B1c | global, no owner, no justification | hoarding lint finding | test_B1c_unjustified_global_flagged |
| B1d | global + valid justification | clean | test_B1d_justified_global_is_clean (+all_justifications param) |
| B2 | owner not registered | unknown-owner | test_B2_unknown_owner_flagged |
| B2b | global + profile copies | duplicate/hoarding | test_B2b_global_and_profile_copies_flagged |
| B3 | N=500 fleet scan | <10s, no partial state | test_B3_scan_500_skills_under_10s |
| B3b | concurrent scan + create | no race/lost findings | test_B3b_concurrent_scan_and_create |
| B4 | propose-fix | proposes, never auto-mutates | test_B4_proposes_never_auto_mutates |
| B4b | audit via REST | 202 + run_id; feed; idempotent | test_B4b_audit_run_rest_contract |

---

## Run 1 — 2026-08-24 (BUILD-4 harness delivery)

- **Candidate**: /home/tony/projects/skill-routing-plugin @ `9d3b498` (engine) with uncommitted BUILD-2 dashboard (`e708d06` landed during the run; tests ran against the committed engine + as-found untracked dashboard)
- **Core**: /home/tony/.hermes/hermes-agent @ `057dcdf236` (runtime venv python 3.11.15)
- **OS**: Linux 7.1.3-arch2-2 (Arch)
- **Command**: `/home/tony/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q` (dev suites) + `tests/qa/` (QA harness)

### Gate A results

| Row | Verdict | Notes |
|-----|---------|-------|
| A1  | **FAIL** | deny-redirect message lacks pinned `'Hand the skill creation to'` — engine says "Use the skill_owner_create tool…"; routed create itself works (probe: SKILL.md+usage+ledger land under trt, default clean) |
| A2  | PASS | |
| A3  | PASS | both pinned substrings present |
| A3b | PASS ×3 | edit/delete/archive all denied; FS untouched (hash-snapshot proof) |
| A3c | PASS ×3 | |
| A3d | PASS ×3 | |
| A4  | PASS | |
| A5  | PASS | |
| A6  | PASS ×3 | |
| A6b | PASS ×6 | all six payloads denied at validation; FS hash-snapshots identical |
| A7  | PASS | specialist-side `enabled: false` config ignored; fleet rule holds |
| A7b | PASS | dormancy active while (simulated) core enforces; logged exactly once over 3 calls |
| A8  | PASS | create + mutation gates bypass incl. drifted legacy skill |
| A8b | PASS | key absent → ENABLED, require-guidance denial |
| A8c | **FAIL** | SPEC-3 v1.0 pins "no deny, no route" (refusal posture); engine DENIES with "refuse cross-profile creation" (upstream e12d79edd1 behavior) |
| A8d | PASS | ownerless create allowed; scan surfaces it as `unowned` |
| A8e | PASS | |
| A9  | PASS | p95 <50ms over N=1100 non-skill_manage calls; zero-I/O proven by bombing open/stat/scandir/Path.exists — no assertion fired |
| A9b | **FAIL** | round trip functionally correct (probe evidence in Run-1 notes); message contract divergence as A1 |

### Gate B results

| Row | Verdict | Notes |
|-----|---------|-------|
| B1  | PASS | |
| B1b | PASS | |
| B1c | PASS | engine kind: `unowned` (SPEC-0 enum); SPEC-3 B1c row labels it "unjustified-global" — kind-label divergence, recorded below |
| B1d | PASS ×3 | control-plane / shared-primitive / verified-structural-dependency all clean |
| B2  | PASS | |
| B2b | PASS | raises BOTH `unowned` (global copy) AND `duplicate/hoarding` — duplicate/hoarding present as pinned |
| B2b-note | — | global+profile duplicate emits two findings (unowned on the global copy + duplicate/hoarding). Acceptable; matrix asserts the pinned kind is present |
| B3  | PASS | 500-skill scan ≪10s (suite ran in 0.16s incl. fixture); `scanned`==500 exactly, zero dropped/extra findings vs expected drift set |
| B3b | PASS | 5 scans × 10 creates threaded; no errors, seed drift survives, all created skills present |
| B4  | PASS | proposed_fix present on all findings; fleet skill content unchanged (plugin-owned ledger sidecar excluded by design) |
| B4b | PASS | 202 + run_id; idempotent re-run 202; findings visible in feed (mounted at the SPEC-2 prefix) |

### Harness self-checks — 11/11 PASS

Fixture layout, real active-profile inference, config-writer knob matrix (incl. absent-key forms), real core `skill_manage` create under temp default AND profile homes, skill placement scopes, snapshot mutation detection, root-skills resolution, runtime fingerprint, and the ground-truth anchor (installed core lacks e12d79edd1 symbols → A7b simulation is valid).

### Run 1 findings (spec-vs-engine divergences — developer decision required)

1. **F1 (A1/A9b, Medium)**: deny-redirect message (default→specialist create) does not contain SPEC-3's pinned `'Hand the skill creation to'`. Engine text: "Use the skill_owner_create tool (name=…)". The engine's redirect is functionally complete and arguably clearer, but SPEC-3 §Message-contract pins the substring for A3/A9b. Fix: either add the phrase to gate.py's redirect block message or amend SPEC-3. Owner: developer (message text) — or spec amendment via qa.
2. **F2 (A8c, Medium)**: SPEC-3 v1.0 A8c pins "plain create stays under default (refusal posture; no deny, no route)"; engine denies with "configured to refuse cross-profile creation" (faithful to upstream e12d79edd1). This is the known upstream/plugin posture question — needs an explicit ruling: amend SPEC-3 to match upstream semantics, or change engine behavior. Owner: developer + spec amendment.
3. **F3 (B1c, Low)**: SPEC-3's B1c row says finding kind "unjustified-global"; SPEC-0 Interface 3 enum (binding) has no such kind — engine uses `unowned`. Spec-internal inconsistency; engine follows the binding enum. Owner: spec amendment (qa/spec owner) — engine compliant as-is.
4. **F4 (B2b, Low)**: duplicate skill raises both `unowned` + `duplicate/hoarding`. Both valid per the analysis rules; informational.

### Blocked / not applicable this run

- **A7b core-branch SHA**: dormancy tested via symbol simulation (monkeypatch of `_skill_owner_routing_policy`), not a real core branch. SPEC-3 asks for the core test-branch SHA for A7b — the worktree `hermes-agent-skill-owner-pr` @ `2015748f7d` exists for a future real-core run.
- **Gate C / Gate D**: out of scope for BUILD-4 (dashboard/UI acceptance is Gate C; release readiness Gate D).

### Reproduction

```
cd /home/tony/projects/skill-routing-plugin
/home/tony/.hermes/hermes-agent/venv/bin/python -m pytest tests/qa/ -q
```

---

## Run 2 — 2026-08-24 10:02 CDT (post-crash reproduction; BUILD-4 resumed run)

Run 1's agent crashed before committing; this run re-verified everything from
the surviving tree before delivery.

- **Candidate/fingerprint**: identical to Run 1 — plugin `e708d06` (dirty: the
  uncommitted harness files themselves), core `057dcdf236`, Linux
  7.1.3-arch2-2, Python 3.11.15.
- `bash qa/run_gates.sh` (full `tests/`): **3 failed, 167 passed in 1.98s** —
  exactly the A1 / A8c / A9b spec-pin assertions of Run 1.
- `tests/qa/` alone: **3 failed, 52 passed** → Gate A 29/32, Gate B 12/12,
  self-check 11/11 — identical verdict set to Run 1.
- **Determinism**: two independent full runs (plus Run 1's original) produce
  the same 3 failures with the same assertion messages; no flakiness observed.
- Findings F1–F4 unchanged; routed to developer (see card t_d4ecf065
  completion).

---

## Appendix: QA harness layout

- `tests/qa/contracts.py` — shared seams: engine module names, decision vocabulary, pinned message substrings, config writer, temp-fleet fixtures, FS snapshot
- `tests/qa/hook_driver.py` — drives the plugin the way core `invoke_hook` does; finding-record validation per SPEC-0 Interface 3
- `tests/qa/engine_discovery.py` — engine-presence skip logic (auto-activates when BUILD-1 lands; now moot — engine present)
- `tests/qa/test_gate_a.py` — 19 Gate A rows (32 test items)
- `tests/qa/test_gate_b.py` — 12 Gate B rows (12 test items)
- `tests/qa/test_harness_selfcheck.py` — 11 harness self-tests (engine-independent)
- `tests/qa/conftest.py` — QA fleet fixture (real active-profile inference; deliberately shadows dev fixture)
- `qa/run_gates.sh` — fingerprinted runner (core commit, plugin commit, OS, Python)

---

## Run 3 — 2026-08-24 ~10:20 CDT (post-repair; t_4f4ff38c)

Candidate: plugin `b950d21` (clean tree), core **`057dcdf236`** (pinned via
`SORE_CORE_ROOT=/tmp/core-morning-057dcdf` worktree), Linux 7.1.3-arch2-2,
Python 3.11.15.

### Repairs delivered (F1/F2/F3 + manifest + harness)
- **F1**: engine deny-redirect message now carries pinned phrase
  "Hand the skill creation to" ahead of skill_owner_create guidance
  (`gate.py`, one block message).
- **F2 ruling**: SPEC-3 A8c amended to upstream e12d79edd1 refusal
  semantics (route_from_default:false → DENY + "refuse cross-profile
  creation; delegate the create"); test_A8c updated; substring added to
  SPEC-3 message-contract list.
- **F3**: SPEC-3 B1c relabeled to SPEC-0 enum kind `unowned`; test_B1c
  exact-matches the enum.
- **Harness (latent)**: `get_routed_create` seam never found
  `skill_owner_routing.register.register()` (probed only `.plugin` /
  `__init__`), falling back to the raw `routed_create()` function and
  TypeError-ing on the args-dict call — unmasked by F1 because A1/A9b
  previously failed earlier at the message assertion. Seam fixed.
- `dashboard/manifest.json` committed (BUILD-2 e708d06 referenced it,
  never committed it).

### Result
`bash /tmp/repair_evidence.sh` (full `tests/`, pinned core): **170 passed,
0 failed** (was 3 failed / 167 passed pre-repair). Gate A 32/32, Gate B
12/12, self-check 11/11, dev suite 115/115.

### NEW FINDING (out of repair scope → F5, routed to owner)
The local core checkout moved `057dcdf236` → `809e94ca4c` at 10:04 CDT
today (desktop offbox work). New core contains **merged PR #87101**
(`f54a6a729b` "feat(skills): route learned skills by owner profile":
`config_defaults.py` ships `skills.owner_routing.enabled: False`;
`tools/skill_manager_tool` carries the routing symbols). Running the same
harness against that core: 18 failed / 37 passed in `tests/qa/` — this is
SPEC-3 **D5 (core-upgrade dormancy) arriving for real**:
- absent-key=ENABLED diverges (core defaults supply `enabled: False`);
- dormancy probe fires (create gate returns None — no double-deny, working
  as designed) so A1–A9b deny assertions fail while core's own routing
  enforces instead;
- self-check `test_installed_core_lacks_pr_symbols` now false by design.
Re-baselining A-rows against enforcing core (and the default-ON posture
decision vs core default-OFF) is a spec/verification question for QA, not a
repair. Matrix rows should pin `SORE_CORE_ROOT` to the certifying core.

---

## Run 4 — 2026-08-24 ~11:0x CDT (QA independent re-verification, t_2f4cee94)

Candidate: plugin master @ `b017de0` (clean tree; fix `b950d21` and evidence
`b017de0` both verified ancestors of HEAD). Core: **pinned `057dcdf236`** via
`SORE_CORE_ROOT=/tmp/core-morning-057dcdf` (worktree recreated this run —
/tmp had been swept since Run 3). **Import pin proven by probe**: under the
pin, `tools.skill_manager_tool` resolves from the worktree and LACKS the PR
symbols; unpinned it resolves from live `809e94ca4c` WITH them (the pin
shadows both pytest.ini pythonpath and the venv editable install).

Commands + results (venv Python 3.11.15, Linux 7.1.3-arch2-2):
- pinned `pytest tests/ -q` → **170 passed** (run twice: 2.25s / 1.93s — deterministic)
- pinned `pytest tests/qa/ -q` → 55 passed (Gate A 32/32, Gate B 12/12, self-check 11/11)
- unpinned `pytest tests/qa/ -q` → **18 failed / 37 passed** — failure set
  identical to Run 3's live-core note: 16 create-gate deny-expectation rows
  (A1, A3, A4, A5, A6×3, A6b×6, A7, A8c, A9b) defeated by dormancy +
  A8b posture divergence + self-check anchor
- live-core behavior probes (temp HERMES_HOME, real core `skill_manage`):
  - key ABSENT → plugin reads `enabled:False` (core defaults merged through
    `load_config`); create lands LOCALLY despite `owner_profile:trt`
  - `enabled:true` → dormancy fires; plugin gate returns None (no
    double-deny); **core itself performs the routed create** (success, lands
    in trt home)
  - `enabled:false` → plugin and core both bypass identically

### F1/F2/F3 re-verification — all PASS
- **F1**: "Hand the skill creation to" present in `gate.py` default→specialist
  redirect (A3 sideways message also carries it). A1/A9b green.
- **F2**: SPEC-3 A8c amendment mirrors upstream `e12d79edd1` test exactly
  (`success:false` + "configured to refuse cross-profile creation" +
  `create.assert_not_called()`); engine text matches; test asserts deny +
  refusal substring + neither local nor routed landing. Drift rationale sound
  (a local create of an owner-declared skill is instant B1 drift).
- **F3**: `unowned` is the SPEC-0 Interface 3 enum kind; `unjustified-global`
  never existed in the enum. test_B1c exact-matches.
- **Harness seam**: `get_routed_create` now probes
  `skill_owner_routing.register`; A9b round trip green.
- `dashboard/manifest.json` committed in b950d21 and tracked. ✓

### F5 RULING (QA — core #87101 merged mid-cycle)
- **R1 — certification stays pinned to the pre-#87101 core.** Gate A
  certifies PLUGIN enforcement semantics; on enforcing cores the create gate
  is dormant by design, so those rows are only meaningfully testable against
  a pre-#87101 core. The 18 live-core failures are SPEC-3 D5 arriving live,
  NOT regressions of b950d21. Every future run header records
  `SORE_CORE_ROOT` + the resolved import root.
- **R2 — posture**: on cores ≥ #87101, ACCEPT merged-defaults semantics
  (absent key = disabled; fleets opt in via explicit `enabled:true`).
  Single-reader coexistence outranks install-time default-ON; the
  alternative (direct-YAML-file read resurrecting default-ON on new cores)
  makes one config mean two things and reverses the documented Tony ruling —
  needs Tony sign-off if ever wanted. A8b/D4b re-baseline
  core-generation-conditional. [amendment → developer]
- **R3 — D5 provenance wording**: upstream core does NOT deny under
  `route_from_default:true` — it performs the routed create itself (probe
  evidence above). Re-word D5: "a create attempt yields at most ONE
  enforcement action originating from core (routed create OR denial);
  plugin gate returns None and audit-logs only". D5/D5b/D5c are now runnable
  for real at Gate D (QA-L3) against `809e94ca4c`+. [amendment → developer]

### New Low findings (non-blocking, route: developer)
- **L1**: `tests/qa/conftest.py` hardcodes `CORE` to the live checkout — the
  fingerprint self-test printed `CORE=809e94ca4c` during a provably pinned
  run. Derive `CORE` from `SORE_CORE_ROOT` when set.
- **L2**: `test_A8c_route_from_default_false_stays_on_default` name predates
  the F2 amendment (behavior is now DENY); rename at convenience.

**VERDICT: PASS (QA-L2)** — BUILD-4 Gate A/B accepted vs pinned core
`057dcdf236`; F1/F2/F3 fixed; F5 ruled (not a candidate defect). Gate C/D
remain release-gate scope per Run 1 notes.
