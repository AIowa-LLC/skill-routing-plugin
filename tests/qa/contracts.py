"""Shared contracts for the skill-owner-routing QA harness.

Single source of truth for:

* module names the BUILD-1 engine must provide (SPEC-1 package layout);
* exact decision/action vocabulary the plugin hook must understand;
* stable message-contract substrings (SPEC-3 §Message-contract assertions);
* temp HERMES_HOME fixture helpers (real imports, real FS — no mocks for
  anything except gateway/websocket externals).

If BUILD-1 lands with different names, update ONLY this file — every test
module imports the seams from here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Engine location. Order: env override → repo checkout → ~/.hermes/plugins/.
# The engine is expected at <plugin_root>/skill_owner_routing/ per SPEC-1.
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent.parent
_PLUGIN_ROOT_CANDIDATES = [
    Path(os.environ.get("SKILL_ROUTING_PLUGIN_ROOT", "")) if os.environ.get("SKILL_ROUTING_PLUGIN_ROOT") else None,
    _REPO,                                    # repo checkout (BUILD output lands here)
    Path.home() / ".hermes" / "plugins" / "skill-owner-routing",  # installed form
]


def plugin_root() -> Path:
    for cand in _PLUGIN_ROOT_CANDIDATES:
        if cand is None:
            continue
        pkg = cand / "skill_owner_routing"
        if pkg.is_dir():
            return cand
    return _REPO  # fall through; engine-present check reports NOT_PRESENT


def engine_path(name: str) -> Path:
    return plugin_root() / "skill_owner_routing" / f"{name}.py"


def engine_present() -> bool:
    return engine_path("gate").is_file()


# ---------------------------------------------------------------------------
# Module names (SPEC-1 §Package layout). Do not scatter literals.
# ---------------------------------------------------------------------------

MOD_POLICY = "skill_owner_routing.policy"
MOD_FRONTMATTER = "skill_owner_routing.frontmatter"
MOD_GATE = "skill_owner_routing.gate"
MOD_ROUTED_CREATE = "skill_owner_routing.routed_create"
MOD_DRIFT = "skill_owner_routing.drift"
MOD_HOARDING = "skill_owner_routing.hoarding"

ENGINE_MODULES = [
    MOD_POLICY,
    MOD_FRONTMATTER,
    MOD_GATE,
    MOD_ROUTED_CREATE,
    MOD_DRIFT,
    MOD_HOARDING,
]


def ensure_engine_importable() -> None:
    """Make <plugin_root> importable as a package parent (idempotent)."""
    root = plugin_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


# ---------------------------------------------------------------------------
# Decision / action vocabulary
# ---------------------------------------------------------------------------

ACTION_BLOCK = "block"
ACTION_APPROVE = "approve"
ACTION_ESCALATE = "approve"  # legacy alias name for the same directive


# ---------------------------------------------------------------------------
# Stable message-contract substrings (SPEC-3). These are the canonical
# upstream sentences from PR #87101 / commit e12d79edd1 — pin substrings,
# never full strings.
# ---------------------------------------------------------------------------

MSG_SIDEWAYS = "may not write skills sideways"
MSG_NOT_REGISTERED = "is not registered"
MSG_OWNER_PROFILE_KEY = "owner_profile"
MSG_HANDOFF = "Hand the skill creation to"
MSG_RESOLVE_OWNER = "Could not resolve skill owner profile"
MSG_REFUSE_ROUTE = "refuse cross-profile creation"
MSG_REQUIRE_METADATA = (
    "Skill owner routing is enabled. New skills must declare "
    "metadata.hermes.owner_profile"
)
MSG_REQUIRE_METADATA_SHORT = "owner_profile"


# ---------------------------------------------------------------------------
# Config contract
# ---------------------------------------------------------------------------

CONFIG_KEY = "skills.owner_routing"  # in the DEFAULT profile config.yaml


def write_default_config(home: Path, *, enabled=None, require_owner_metadata=None, route_from_default=None) -> Path:
    """Write a config.yaml for the fleet-default home.

    Keys left as None are OMITTED (this is how A8b "key absent" and the
    per-knob-absent variants are built). enabled=None + others None writes
    no skills.owner_routing subtree at all.
    """
    lines = ["# written by QA harness"]
    owner_lines = []
    if enabled is not None:
        owner_lines.append(f"    enabled: {'true' if enabled else 'false'}")
    if require_owner_metadata is not None:
        owner_lines.append(f"    require_owner_metadata: {'true' if require_owner_metadata else 'false'}")
    if route_from_default is not None:
        owner_lines.append(f"    route_from_default: {'true' if route_from_default else 'false'}")
    if owner_lines:
        lines.append("skills:")
        lines.append("  owner_routing:")
        lines.extend(owner_lines)
    cfg = home / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# Temp-fleet fixture (real imports, real FS)
# ---------------------------------------------------------------------------

VALID_SKILL_CONTENT = """---
name: test-skill
description: A test skill for unit testing.
---

# Test Skill

Step 1: Do the thing.
"""

OWNER_ROUTED_SKILL_CONTENT = """---
name: routed-skill
description: A routed test skill.
metadata:
  hermes:
    owner_profile: trt
---

# Routed Skill

Step 1: Verify ownership routing.
"""


def make_fleet(tmp_path: Path, *, profiles=("trt", "growth")) -> Path:
    """Create a temp fleet root and return it.

    Sets HERMES_HOME=<root> for the process (default home). Callers that
    need a specialist context re-point os.environ["HERMES_HOME"] and reload
    the profile-sensitive modules (see switch_profile()).
    """
    root = tmp_path / "hermes"
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    for p in profiles:
        (root / "profiles" / p / "skills").mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(root)
    # Pre-seed core's bootstrap SOUL.md so its lazy seeding (load_config →
    # seed_soul) can't pollute FS-snapshot no-mutation assertions. Content
    # is arbitrary; core only writes it when ABSENT.
    for home in [root, *(root / "profiles" / p for p in profiles)]:
        soul = home / "SOUL.md"
        if not soul.exists():
            soul.write_text("# fixture\n", encoding="utf-8")
    return root


def profile_home(root: Path, name: str) -> Path:
    return root / "profiles" / name


def switch_profile(name: str, root: Path) -> None:
    """Point HERMES_HOME at a specialist profile dir under a temp root.

    get_active_profile_name() infers the profile from the home path, so this
    is the real inference path, not a mock.
    """
    if name in ("default", ""):
        os.environ["HERMES_HOME"] = str(root)
    else:
        os.environ["HERMES_HOME"] = str(root / "profiles" / name)


def make_skill(home: Path, name: str, owner: str | None, body: str = "# Body\n", *, scope: str = "profile", justification: str | None = None) -> Path:
    """Create a SKILL.md under <home>/skills/<name>/ for fixture setup.

    scope="global" places it under the fleet root skills dir instead (the
    hoarding-lint surface). owner=None omits the metadata block entirely.
    """
    meta = ""
    if owner or justification:
        meta = "metadata:\n  hermes:\n"
        if owner:
            meta += f"    owner_profile: {owner}\n"
        if justification:
            meta += f"    global_justification: {justification}\n"
    content = (
        f"---\nname: {name}\ndescription: Fixture skill {name}.\n{meta}---\n\n# {name}\n\n{body}"
    )
    base = root_skills_dir(home) if scope == "global" else home / "skills"
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(content, encoding="utf-8")
    return md


def root_skills_dir(home: Path) -> Path:
    """Return the fleet-root skills dir for a profile-scoped home.

    Per hermes_constants.get_default_hermes_root(): when HERMES_HOME is
    <root>/profiles/<name>, the root is the grandparent... of the parent
    'profiles' dir — i.e. <root>. get_skills_dir() under the profile home
    is <home>/skills; the fleet-wide (global) surface is <root>/skills.
    """
    if home.parent.name == "profiles":
        return home.parent.parent / "skills"
    return home / "skills"


def routed_content(owner: str, name: str = "routed-skill") -> str:
    """OWNER_ROUTED_SKILL_CONTENT with an arbitrary owner/name (A5/A6/A6b/A8e)."""
    return OWNER_ROUTED_SKILL_CONTENT.replace(
        "owner_profile: trt", f"owner_profile: {owner}"
    ).replace("name: routed-skill", f"name: {name}")


def snapshot_tree(root: Path) -> dict:
    """Map of relative-path → sha256 for every file under root.

    Used to prove denied/injection payloads never mutated the fleet FS.
    """
    import hashlib

    snap = {}
    for p in sorted(root.rglob("*")):
        # dot-files are sidecars/ledgers (usage, curator ledger, plugin
        # findings store) — plugin-owned state, not fleet skill content
        if p.is_file() and not p.is_symlink() and not p.name.startswith("."):
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def read_yaml(path: Path) -> dict:
    """Minimal YAML reader for flat + one-nested-dict config files we write."""
    import yaml

    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
