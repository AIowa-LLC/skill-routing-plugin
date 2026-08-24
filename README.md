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
