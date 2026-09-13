# skill-owner-routing

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that keeps
fleet skills where they belong. If you run multiple Hermes profiles, it enforces
per-profile skill **ownership**: skills are created in the profile that owns the
capability, never edited "sideways" from the wrong profile, and audited for
ownership drift on demand.

It ships as one unified plugin package — an enforcement engine (Python), a REST
+ WebSocket dashboard API, and an opt-in desktop-app UI — that installs and
uninstalls as a single folder.

- **Enforcement gate** — a `pre_tool_call` hook intercepts every
  `skill_manage` call (create, edit, patch, delete, write_file, remove_file,
  archive). Creates that declare an `owner_profile` are routed into the
  owner's home; mutations of a skill from the wrong profile are blocked with
  guidance on doing it correctly. The gate fails closed: a malfunction blocks
  the call and says so, distinctly from a policy denial.
- **Ownership-drift audit** — an on-demand scanner over every profile's
  `skills/` tree plus the global home, reporting five finding kinds:
  `drifted`, `misplaced-global`, `unknown-owner`, `duplicate/hoarding`,
  `unowned` (severity low / medium / high). Findings land in a deterministic
  ledger — re-scans are idempotent, and resolving one finding never resolves
  another.
- **Dashboard** — a REST API (ownership map, drift feed, audit runner, policy
  panel) mounted under the Hermes web dashboard, plus live invalidation over a
  WebSocket; and a desktop-app pane with the ownership map, drift feed, and
  policy controls.

MIT licensed — see [LICENSE](LICENSE). Architecture and interface details:
[specs/](specs/). Nothing here phones home: the plugin makes no outbound
network connections and reads only skill metadata inside your Hermes home
(details in [Security](#security--data-scope)).

## Requirements

- A working Hermes install (CLI or gateway) — the engine imports Hermes core
  modules at runtime; it is not a standalone library.
- Python ≥ 3.11 — use the same interpreter/venv that runs your Hermes
  gateway.
- The engine itself is stdlib-only. The dashboard API additionally uses
  PyYAML, FastAPI, and pydantic — all installed with Hermes core itself, so a
  normal Hermes venv already has them. Only unusual stripped-down
  installations need `pip install PyYAML fastapi pydantic` (exact ranges in
  [pyproject.toml](pyproject.toml)) into that venv.

## Install

```bash
# from a git URL (custom source — the Hermes catalog is the reviewed alternative)
hermes plugins install https://github.com/AIowa-LLC/skill-routing-plugin.git
```

The installer clones into `$HERMES_HOME/plugins/skill-owner-routing/`, offers
to enable the plugin, and tells you to restart the gateway. Equivalent manual
steps:

```bash
git clone https://github.com/AIowa-LLC/skill-routing-plugin.git \
    "$HERMES_HOME/plugins/skill-owner-routing"
hermes plugins enable skill-owner-routing   # installing is the opt-in; this flips it on
hermes gateway restart
```

`hermes plugins list` should now show `skill-owner-routing` as enabled.

**Desktop app UI (optional).** The desktop half ships in the same folder and
inventories automatically, but it is opt-in: open **Settings → Desktop
plugins** in the Hermes desktop app and toggle **Skill Ownership** on (toggles
apply live; if the pane does not appear, run **Reload desktop plugins** from
the command palette). The Python half and desktop half enable independently —
the dashboard REST API follows `hermes plugins enable`; the pane follows the
in-app toggle.

**pip install (optional).** Only needed for standalone/dashboard-only
deployments that import the package outside Hermes:
`pip install -e .` (add `.[web]` to also pull uvicorn).

## Quickstart

1. **Run an audit.** Ask your agent: *"Run a skill ownership audit"* — the
   plugin registers a `skill_owner_audit` tool (actions: `scan`, `list`). Or
   run the engine directly from a shell:

   ```bash
   cd "$HERMES_HOME/plugins/skill-owner-routing"
   <hermes-venv-python> -c "from skill_owner_routing.drift import watchdog_main; raise SystemExit(watchdog_main())"
   ```

   Clean fleet → exit 0, no output. Findings → a JSON digest on stdout, e.g.
   `[medium] misplaced-global: stray-skill (actual=default) …`. Findings are
   persisted to `$HERMES_HOME/skills/.skill_owner_findings.json`.

2. **Open the dashboard.** With the plugin enabled, the Hermes web dashboard
   (`hermes dashboard`) serves the ownership API under
   `/api/plugins/skill-owner-routing/` — ownership map, drift feed and
   summary, audit runner (`POST /audit/run` returns 202; poll
   `GET /audit/runs/{run_id}`), and policy panel. Scans also run on demand:
   via the `skill_owner_audit` tool, via the dashboard's audit endpoints, or
   automatically as a cold-start seed when no ledger/state exists yet.

3. **Desktop pane.** After enabling the desktop half (above): a **Skill
   Ownership** entry in the sidebar, a drift-count statusbar chip, and command
   palette entries (*Skill Ownership: Open map / Run audit now / Toggle
   policy*). The map and drift feed revalidate live over WebSocket when a
   scan or resolution lands.

4. **Create a skill the routed way.** Give the agent skill content whose
   frontmatter declares an owner:

   ```yaml
   ---
   name: my-skill
   description: What it does.
   metadata:
     hermes:
       owner_profile: trt        # a named profile that owns this capability
   ---
   ```

   While the *default* profile is active, a plain create is denied with a
   redirect to the plugin's `skill_owner_create` tool, which writes the skill
   into `trt`'s home (ledger and usage records included) in one routed
   transaction.

## Configuration

**Policy** lives in the DEFAULT profile's `config.yaml` under
`skills.owner_routing` (the fleet-wide rule; a specialist profile cannot weaken
it):

```yaml
skills:
  owner_routing:
    enabled: true                # default when the key is absent — installing is the opt-in
    require_owner_metadata: true # global skills must carry owner metadata or a justification
    route_from_default: true     # default-profile creates route into the owner's home
```

Editing policy by hand is fine; the dashboard's `PUT /policy` (body:
`{"enabled": …, "confirm": true}` — any subset of the three keys) does the
same edit with a recorded history event.

**Environment variables** (beyond standard Hermes ones like `HERMES_HOME`):

| Variable | Purpose |
|---|---|
| `HERMES_DASHBOARD_SESSION_TOKEN` | Auth credential for the dashboard API in standalone/test mounts, and Hermes' own fixed-token dashboard auth. Sent as `X-Hermes-Session-Token`. Minimum 22 chars / 16 bytes — weaker or unset values fail closed (every route stays protected). Never passed as a URL query parameter. |
| `HERMES_DASHBOARD_PUBLIC_URL` | When the dashboard is exposed behind a public URL, the plugin's WebSocket additionally accepts exactly this origin. |
| `SORE_CORE_ROOT`, `HERMES_VENV_PY` | Development/QA only — point the test suite and gate runner at a Hermes core checkout and its venv. Not needed for installed use. |

## Security & data scope

The plugin is deliberately boring about your data:

- **No network egress.** It serves the dashboard API when Hermes mounts it,
  and makes no outbound connections anywhere else. `network: []` is declared
  in the manifest.
- **Read scope:** skill `SKILL.md` frontmatter (name, description,
  `metadata.hermes.owner_profile`, `global_justification`) and file paths —
  only inside your `$HERMES_HOME` skills trees — plus its own ledger, state,
  and the `skills.owner_routing` policy. **Write scope:** that policy subtree,
  resolution status, audit state, and the findings ledger. Nothing else. No
  secret access (`secrets: []`).
- **Redaction at the API boundary.** Detail responses expose an allowlist
  only — skill name, a length-capped description, a validated owner profile,
  a justification enum. SKILL.md bodies, absolute paths, and symlink targets
  never leave the API.
- **Authentication, fail closed.** Every dashboard route requires a
  credential: Hermes' own dashboard auth when host-mounted (session, token, or
  cookie — the plugin accepts the host's verdict), or the exact
  `X-Hermes-Session-Token` in standalone mounts (constant-time compare;
  missing vs. wrong are indistinguishable generic 401s). **Localhost is not an
  auth bypass.** The WebSocket checks Origin (exact loopback origins, or the
  configured public URL) and authentication before accepting any connection.
- **The gate fails closed.** An unexpected engine error during a
  `skill_manage` call blocks that call with a message explicitly labeled as a
  gate malfunction — never silently allowed, never confused with a policy
  violation, and never leaking exception text.
- **Symlink containment.** Skill directories or `SKILL.md` files that are
  symlinks resolving outside the scanned home are skipped with a warning —
  never read or indexed.
- **Least-privilege manifest.** `plugin.yaml` declares all of the above as an
  explicit `permissions:` block; the repo's release validation
  (`tests/security_validator.py`) fails if it is absent, broader, or
  inconsistent with the code.

## Verification & development

```bash
# unit + QA suite — needs a Hermes core checkout for the core imports:
SORE_CORE_ROOT=/path/to/hermes-agent pytest tests/ -q

# Gate A/B evidence runner (appends environment-fingerprinted results to qa/matrix.md)
./qa/run_gates.sh

# desktop plugin contract test (node)
node tests/desktop-plugin-contract.mjs

# manifest sanity for any install
hermes plugins validate "$HERMES_HOME/plugins/skill-owner-routing"
```

CI (`.github/workflows/ci.yml`) runs the same suite against a pinned Hermes
core. The QA evidence history lives in [qa/matrix.md](qa/matrix.md).

## Repository layout

```
skill-owner-routing/
├── plugin.yaml              # manifest: hooks, tools, permissions
├── __init__.py              # sys.path bootstrap + register(ctx)
├── skill_owner_routing/     # engine: policy, gate, routed_create, drift,
│                            #   hoarding, ledger, frontmatter, index, …
├── dashboard/               # REST/WS API (plugin_api.py) + desktop manifest
├── desktop/plugin.js        # desktop-app UI half (opt-in)
├── tests/                   # pytest suite + desktop contract test
├── qa/                      # SPEC-3 gate runner + evidence matrix
└── specs/                   # product/architecture/dashboard/verification specs
```

## Limitations & roadmap

- **No scheduler.** Drift scans are on demand (agent tool, dashboard, or
  cold-start seed). The kit registers no cron job and ships no installer;
  wiring `watchdog_main()` into Hermes cron is a manual, documented-by-recipe
  operator task (silent exit 0 when clean). Scheduled scanning is roadmap.
- The desktop drift drawer is read-only; resolution happens through the feed.
- Authentication is the only access control: any authenticated dashboard
  caller can read the map, resolve findings, and change policy. REST 403 is
  reserved for a future permission model (the WebSocket does use a
  pre-upgrade 403 to reject disallowed Origins).

## License

MIT — see [LICENSE](LICENSE).
