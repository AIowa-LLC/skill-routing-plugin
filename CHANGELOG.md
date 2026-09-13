# Changelog

All notable changes to the skill-owner-routing plugin are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to no strict versioning schedule — releases mark
verified stability points of the audit-hardened kit.

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
