# Changelog

All notable changes to the skill-owner-routing plugin are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to no strict versioning schedule — releases mark
verified stability points of the audit-hardened kit.

## [Unreleased]

v0.2.1 follow-up from the OCR full-repo review (remaining majors +
selected minors), verified on pinned core 3e09e5a15f. The probed
`_scope_of` degenerate-path guard named in the triage was already
covered by the M1/M2 commit on master (gate.py guard + test).

### Concurrency & silent-degradation majors

- Policy load under the lock (M4): `read_policy()` now runs the
  cold-cache `_load()` under `_LOCK` (double-checked locking). `_load()`
  mutates process-global home-override state; concurrent dashboard
  threadpool readers could interleave set/reset (wrong-home read,
  SPEC-1) or leave the override stuck on — and every racer repeated the
  load. Stampede test pins exactly one load under 4 racing readers.
- Home-override env fallback always pins (M7): when the core token
  machinery is unavailable, `_home_override` now always sets
  `HERMES_HOME` (the old code only set it when already set, silently
  no-op'ing otherwise — engine calls then resolved the REAL fleet home,
  the exact wrong-fleet read this module exists to prevent). Exit
  restores the pre-entry state exactly, including "unset".
- Drift parser failures are loud (M8): a broken frontmatter-parser
  import now aborts the scan with RuntimeError (run_audit records
  `state:failed`) instead of silently reclassifying every skill
  fleet-wide as ownerless with zero logging; a single skill's parse
  failure degrades that one skill only, with a warning naming the file.

### QA gate integrity

- Suite runs once (M13): the gate header embedded a full pytest run in
  echo's command substitution — echo's exit status hid that run's
  failure under `set -e`, and the suite ran twice. One run, fatal
  status.
- Fingerprint names the tested core (M14): the core fingerprint now
  uses conftest's resolution (`SORE_CORE_ROOT` → home default); the
  orphaned `HERMES_CORE` var + venv-dir fallback could fingerprint a
  checkout the tests never ran against (observed live: fingerprint
  said 03fee43ca3 while the suite ran pinned 3e09e5a15f).

### Minors

- Unparseable config warns (policy): core's `load_config` never raises
  on broken YAML (serves defaults), so a corrupt config that says
  `enabled: false` silently re-ENABLED routing. Degrade-to-defaults is
  kept (a typo must not freeze mutations) but now logs a loud warning
  naming the file; a merely-absent key stays silent (documented
  default-ON divergence).
- Containment-skip warning rate-limited (drift): the bounded
  `_skip_warns` list never gated the logging — every symlinked/escaping
  skill path logged a warning per scan. Now once per 60s window.

### Remaining OCR minors (v0.2.1 expansion — closes the report)

Grouped remediation of the triaged MINOR section; posture consistent
with the 2026-09-19 fail-closed ruling throughout (A/B/D below).

**Enforcement/engine integrity** — silent degradation of the index
backing the mutation gate is no longer silent: `_within_current_fleet` /
`_profile_roots` resolution failures log (containment still fails
closed, but a shrunk root set is diagnosable); `lookup`'s stale-pop is
compare-and-delete so a drift merge landing mid-lookup can never delete
a fresh entry (the gate would then pass an existing skill's mutation
ungated); the drift→index build resolves duplicate skill names
deterministically (first-seen wins — default home first, matching
`build_index_from_scan`) with a collision warning instead of
lexicographic-last silently winning; `_skills_in` applies ONE symlink
policy (no admitted-then-dropped branch, no spurious containment
warnings); `skill_owner_audit` rejects unknown actions instead of
falling through to a full persistent scan; the hoarding body-marker
regex is derived from `JUSTIFICATION_KEYS`.

**Ledger integrity (M5 family)** — `load→mutate→save` under a module
lock (a stale upsert save can no longer clobber a concurrent
resolution); `save_findings` validates every record through `_valid()`
before disk (persist-then-lose eliminated); `upsert_findings` stamps
`status` itself; `update_status` enforces the status vocabulary
(`'RESOLVED'`-style typos rejected instead of making findings
invisible + silently reverted by the next scan).

**Frontmatter fallback parser (all three PROBED bugs fixed + tests)** —
`hermes:` is honored only under `metadata:` (a `defaults:` block can no
longer donate an owner); quoted values lose their quotes; a closing
fence at EOF without a trailing newline no longer misses the owner.
ImportError (expected degraded env, quiet) is separated from real parse
errors (logged); `resolve_owner_identity` ImportError names the
environment failure instead of reading like an owner rejection.

**routed_create tool contract (M1 family)** — exceptions inside (or
before entering) the override window return a JSON error object (the
tool contract is "returns JSON string"); a failed override reset is
logged loudly; `_active_profile`/`_profile_exists` log swallowed
exceptions; the raw `strip().lower()` normalization guess is replaced
with deny-on-unresolvable.

**home_override residuals (M4/M7 module)** — a failed token reset logs
and clears the override outright (no silent process-global leak);
`_engine_call` logs every failure branch.

**Dashboard API + renderer** — `_broadcast` schedules each socket on
its own loop ((loop, sock) pairs, no cross-product); the cold-start
fleet scan runs outside `_STATE_LOCK` (get_map/get_map_detail no longer
stall the whole dashboard); `_to_ms` converts numeric strings like
numbers (PROBED: epoch-seconds strings rendered Jan-1970); FAILED audit
runs stamp no `last_scan_ts`, and a failed sibling run can no longer
permanently suppress `last_audit_ts`; the dead `_mint_ws_ticket`
plumbing is deleted (it contradicted the README's query-string promise)
— standalone `?ticket=` fails closed, gated host mode defers to the
host's canonical ticket gate, README aligned; the desktop plugin tears
down its `ctx.socket` subscription in `onDispose` (SDK disposer).

**Repo/test hygiene** — CI least-privilege (permissions, concurrency,
timeouts); `.gitignore` secrets patterns; hermetic session token
(hard-set); `qa/run_gates.sh` appends the SPEC-3-promised matrix row on
green runs; the QA `core_commit` fixture skips (not errors) without
git/core; dormancy epoch flips re-arm the log-once announcement.

Deferred (documented, not fixed): `schemas.py` name/category pattern
hardening — needs a core `skill_manage` contract check before
tightening (out of scope here, tracked for v0.2.2+).

## [0.2.0] — 2026-09-19

Pre-release repairs from the OCR full-repo review at v0.2.0-RC (d1659fb),
verified on pinned core 3e09e5a15f (298/298, red-on-base proven).

### Gate fail-closed posture (2026-09-19 ruling)

- Gate fail-closed on actor/scope resolution (M1/M2): failures to resolve
  the active profile or the target skill's location during a
  `skill_manage` call now block with a gate-malfunction message
  (error_code `gate-error`, exception class named, text never leaked)
  instead of silently allowing the mutation. Full tracebacks are logged.
  Reverses the prior "cannot resolve actor — do not invent a denial"
  posture per the 2026-09-19 ruling (OCR M1/M2 + cross-check item 5).
  Also guards the `_scope_of` IndexError on degenerate
  `<home>/profiles` paths (OCR minors).
- Test-fixture identity markers: the dev-suite `fleet` fixture now stamps
  `SOUL.md` profile identity markers, matching the QA harness convention
  — core 19e984ae2a stopped recognizing bare `profiles/<name>/` dirs as
  live profiles, which had reddened 11 create/routed-create tests.

### Repairs landed on the stacked base (t_bbaf8adc)

- Ownership-map virtualization (C1): the desktop map's scroll listener now
  attaches when the rows container mounts — the windowed list previously
  rendered only the first ~24 rows on any real fleet.
- Index traversal containment (M3): traversal-shaped skill names are
  rejected outright and bounded-scan hits must pass the same
  fleet-containment check as cache hits before being returned or cached.
- Ledger fail-loud (M5): unreadable/corrupt/invalid findings ledgers
  block scans and mutations with a clear error instead of silently
  resetting audit history on the next save.
- Policy write guards (M6): `PUT /policy` takes the state lock before
  touching config.yaml, aborts byte-identical on merge failure, and its
  fallback runs only when core config machinery is unavailable — the
  stale-snapshot wipe of the whole config.yaml is gone.
- Hoarding marker boundary (M9): the justification body marker now
  requires a trailing boundary; suffixed spellings (`-legacy`, `-v2`)
  no longer pass the lint.

## [0.2.0] — 2026-09-13

Security, hygiene, and release-mechanics release following the audit
hardening cycle (P2–P8) and pre-release kit polish (m15). The engine
surface is unchanged from the certified Run-5 master; this release makes
the kit portable, documented, and continuously verified.

### Security & trust-path repairs (P5)

- Watchdog crash-resistance (M4): unreadable (chmod-000) directories inside
  a scanned home are skipped, not fatal — readable skills are still
  reported, `watchdog_main` always completes.
- Finding-id path discrimination (M5): drift finding ids now digest the
  resolved skill path, so twin findings (same skill misplaced in two
  homes) get distinct ids; resolving one twin no longer falsely resolves
  the other, and ids remain stable across rescans.
- Resolve endpoint fail-closed (M6): a failed ledger write on
  `POST /drift/{id}/resolve` now returns 5xx naming the exception class,
  records no resolution event, and never reports false success.
- Gate fail-closed (M10): unexpected exceptions in the pre-tool gate block
  with a gate-error message clearly distinct from a policy violation;
  full tracebacks are logged, non-skill tools never reach the engine.

### Correctness repairs

- Dashboard `/map` enumeration is engine-authoritative (P4): rows derive
  from the drift engine's own home/skill discovery (V1-normalized) with a
  faithful port as fallback — phantom twin rows (M1) and invisible nested
  skills (m1) are gone.
- Misplaced-global honors `global_justification` (001eb34): shared
  primitives declared global-with-owner are legitimate, not drift.
- Cross-home symlinked skills/ directories resolve outside the scanned
  home are aliases, not copies — no triple-counting (d4effac, d854723).

### Regression pins (P2)

- New tests pin the 001eb34 justification-honoring fix (with an
  unjustified control), the d4effac alias-skip fix, and the M5 id-collision
  behavior (strict xfail removed; the twin test now pins correct behavior).

### Kit polish (P8, P7 m15 — this release)

- Contract test wired into the gate fail-closed; inert pytest timeout
  removed; routed-create ledger asserts made unconditional; dead config
  removed; environment hygiene tightened; desktop i18n pins added.
- Absolute personal paths stripped from the kit: `SORE_CORE_ROOT` and
  `HERMES_VENV_PY` are now environment-only (portable `Path.home()`
  defaults), `pytest.ini` no longer hardcodes a core checkout, and
  `qa/run_gates.sh` discovers the runtime venv (env → core root → home,
  failing loudly when absent).
- Internal fleet IP (inspection pass + assignment tables) relocated out of
  the repository to private fleet storage.
- Version 0.2.0; CHANGELOG added; GitHub Actions CI added (pinned-core
  pytest run + desktop contract test on push and PR).

### QA certification

- SPEC-3 Gate A/B matrix Run 5: full-state certification of master
  against re-pinned core v0.21.2 (`3e09e5a15f`); 205 tests, 0 failures,
  0 xfail; dev suite 205/205. Historical runs 1–4 carry a HISTORICAL
  banner (older core pins).

## [0.1.0] — 2026-08-24

Initial release: enforcement engine (SPEC-1), desktop UI + REST/WS
dashboard (SPEC-2), and the Gate A/B QA harness with evidence matrix
(SPEC-3).
