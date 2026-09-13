# skill-owner-routing — Hermes plugin enforcing fleet skill ownership.

MIT licensed — see LICENSE.

## Divergence from core PR #87101 (intentional, core-generation-bound)

Plugin treats `skills.owner_routing` key **absent = ENABLED** on pre-#87101
cores (installing the plugin is the opt-in); core PR #87101 treats absent =
disabled. Both honor an explicit `enabled` value identically.

**Core-generation boundary:** on cores ≥ #87101, core ships
`skills.owner_routing.enabled: False` in its defaults, merged through
`load_config`. The plugin's absent-key=ENABLED posture is structurally
unreachable on those cores — fleets opt in via explicit
`skills.owner_routing.enabled: true`. The plugin's own default-ON only
governs pre-#87101 cores where the key is genuinely absent from config.
NOTE: that #87101-carrying core generation existed only briefly (see
"Coexistence + dormancy" below) — current core carries nothing, so the
default-ON posture is the live one.

## Coexistence + dormancy (current posture: plugin FULLY ACTIVE)

The dormancy probe is HISTORICAL. Core PR #87101 (`feat(skills): route
learned skills by owner profile`) was briefly merged into core — observed
at core `809e94ca4c` on 2026-08-24 — and carried
`tools.skill_manager_tool._skill_owner_routing_policy`. Current core
(v0.21.2 @ `3e09e5a15f`, verified 2026-09-13: tree-wide grep for
`owner_routing` returns nothing) carries neither the probe symbol nor the
`skills.owner_routing` config key.

Consequence — BY DESIGN, not by accident (Tony ruling 2026-09-12): on any
core that lacks the probe symbol, the plugin runs FULLY ACTIVE. The create
gate evaluates every `skill_manage{action:create}` call fleet-wide. This
is the accepted posture, not a degraded or unintended mode: on a
single-user fleet the gate IS the product, and there is no second
enforcer to defer to.

The probe itself stays in the code (`skill_owner_routing/coexistence.py`)
and stays cheap (30-second time-boxed cache): should a future core ship
owner-routing enforcement again, the create gate automatically goes
dormant (log-once, audit-only) with no plugin change, and if that core is
later swapped out the gate reactivates on the next decision (SPEC-3 D5c —
no zombie dormancy). Mutation gating (edit/patch/delete/write_file/
remove_file/archive), the drift watchdog, hoarding lint, and the dashboard
never go dormant under any core.

## Layout

```
skill-owner-routing/
├── plugin.yaml            # manifest: kind standalone, hooks + tools
├── __init__.py            # register(ctx): 1 pre_tool_call hook, 2 tools
├── skill_owner_routing/   # engine package
│   ├── policy.py          #   DEFAULT-home policy read, mtime-cached
│   ├── frontmatter.py     #   owner_profile parse/validate
│   ├── gate.py            #   pre_tool_call decision engine
│   ├── routed_create.py   #   routed-create transaction (ported e12d79edd1)
│   ├── drift.py           #   rebuilt ownership-drift watchdog
│   ├── hoarding.py        #   No-Specialist-Hoarding justification lint
│   └── ledger.py          #   findings ledger (DEFAULT home, atomic writes)
└── tests/                 # pytest, temp HERMES_HOME
```

QA evidence: `qa/matrix.md` — append-only run history with pinned core
commits and verdicts (see pin policy there).
