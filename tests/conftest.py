"""Shared fixtures: temp HERMES_HOME fleet + core on sys.path.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = Path(os.environ.get("SORE_CORE_ROOT") or Path.home() / ".hermes" / "hermes-agent")

# Core modules (hermes_constants, hermes_cli.*, tools.*, agent.*, utils)
# live in the Hermes checkout; the plugin imports them lazily.
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

OWNER_ROUTED_SKILL_CONTENT = """\
---
name: routed-skill
description: A routed test skill.
metadata:
  hermes:
    owner_profile: trt
---

# Routed Skill

Step 1: Verify ownership routing.
"""

VALID_NO_OWNER_CONTENT = """\
---
name: plain-skill
description: A plain skill without owner metadata.
---

# Plain Skill

Step 1: Do the thing.
"""


@pytest.fixture()
def fleet(tmp_path, monkeypatch):
    """A temp DEFAULT home + named profiles trt/ and growth/."""
    root = tmp_path / "hermes"
    (root / "profiles" / "trt").mkdir(parents=True)
    (root / "profiles" / "growth").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(
        "hermes_cli.profiles.get_active_profile_name", lambda: "default"
    )

    from skill_owner_routing import common

    common.reset_caches()
    yield {"root": root, "trt": root / "profiles" / "trt",
           "growth": root / "profiles" / "growth", "default": root}
    common.reset_caches()


# --- P7 security-contract test credential ---------------------------------
# Fixed high-entropy token for the whole suite (SECURITY-CONTRACT §REST:
# HERMES_DASHBOARD_SESSION_TOKEN as X-Hermes-Session-Token). Existing
# functional tests send it via AuthedTestClient; the security tests
# additionally exercise the no-credential and wrong-credential paths.
TEST_SESSION_TOKEN = "test-session-token-0123456789abcdef0123456789abcdef"
TEST_SESSION_HEADER = "X-Hermes-Session-Token"


@pytest.fixture(scope="session", autouse=True)
def _security_contract_token():
    os.environ.setdefault("HERMES_DASHBOARD_SESSION_TOKEN", TEST_SESSION_TOKEN)
    yield


@pytest.fixture()
def enabled_config(fleet):
    fleet["root"].joinpath("config.yaml").write_text(
        "skills:\n"
        "  owner_routing:\n"
        "    enabled: true\n"
        "    require_owner_metadata: true\n"
        "    route_from_default: true\n",
        encoding="utf-8",
    )
    return fleet


def write_skill(home: Path, name: str, owner: str = None, extra_fm: str = "") -> Path:
    """Drop a skill into a home's skills dir; returns its SKILL.md path."""
    owner_block = f"    owner_profile: {owner}\n" if owner else ""
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: Skill {name}.\n"
        "metadata:\n"
        "  hermes:\n"
        f"{owner_block}{extra_fm}"
        "---\n\n"
        f"# {name}\n\nBody.\n"
    )
    skill_dir = home / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return skill_md


def set_active_profile(monkeypatch, fleet, name: str) -> None:
    """Simulate the named profile being active (HERMES_HOME stays at fleet
    root so DEFAULT-home config reads keep working — same approach as the
    core e12d79edd1 test matrix)."""
    import hermes_cli.profiles as profiles_mod

    monkeypatch.setattr(profiles_mod, "get_active_profile_name", lambda: name)
