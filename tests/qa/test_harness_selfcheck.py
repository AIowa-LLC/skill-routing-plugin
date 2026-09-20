"""Harness self-checks — run green REGARDLESS of engine presence.

These prove the harness's own machinery: fixture fleet, profile switching,
real core skill_manage under temp home, snapshot/no-mutation proof, and the
runtime fingerprint. They are NOT Gate A/B rows; they are the evidence that
the harness is ready the moment BUILD-1 lands.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from qa.contracts import (
    OWNER_ROUTED_SKILL_CONTENT,
    VALID_SKILL_CONTENT,
    make_fleet,
    make_skill,
    profile_home,
    root_skills_dir,
    snapshot_tree,
    switch_profile,
    write_default_config,
)


def test_fleet_fixture_lays_out_profiles(fleet):
    assert fleet.is_dir()
    assert (fleet / "skills").is_dir()
    assert (profile_home(fleet, "trt") / "skills").is_dir()
    assert (profile_home(fleet, "growth") / "skills").is_dir()
    import hermes_constants

    assert hermes_constants.get_hermes_home() == fleet


def test_profile_switch_drives_real_active_name(fleet):
    import hermes_cli.profiles as profiles

    switch_profile("default", fleet)
    assert profiles.get_active_profile_name() == "default"
    switch_profile("trt", fleet)
    assert profiles.get_active_profile_name() == "trt"
    switch_profile("growth", fleet)
    assert profiles.get_active_profile_name() == "growth"


def test_config_writer_knob_matrix(fleet, tmp_path):
    # full subtree
    write_default_config(fleet, enabled=True, require_owner_metadata=True, route_from_default=True)
    text = (fleet / "config.yaml").read_text()
    assert "owner_routing" in text and "enabled: true" in text
    # key absent entirely
    write_default_config(fleet)
    text = (fleet / "config.yaml").read_text()
    assert "owner_routing" not in text
    # partial knob presence
    write_default_config(fleet, require_owner_metadata=False)
    d = (fleet / "config.yaml").read_text()
    assert "require_owner_metadata: false" in d and "enabled" not in d


def test_core_skill_manage_real_create_under_temp_home(fleet):
    import tools.skill_manager_tool as smt

    switch_profile("default", fleet)
    res = json.loads(
        smt.skill_manage(action="create", name="selfcheck-skill", content=VALID_SKILL_CONTENT)
    )
    assert res["success"] is True, res
    md = fleet / "skills" / "selfcheck-skill" / "SKILL.md"
    assert md.is_file()
    usage = json.loads((fleet / "skills" / ".usage.json").read_text())
    assert "selfcheck-skill" in usage
    ledger = (fleet / "skills" / ".curator_ledger.jsonl").read_text()
    assert "selfcheck-skill" in ledger


def test_core_create_under_profile_home(fleet):
    import tools.skill_manager_tool as smt

    switch_profile("trt", fleet)
    res = json.loads(
        smt.skill_manage(action="create", name="trt-local", content=VALID_SKILL_CONTENT)
    )
    assert res["success"] is True, res
    assert (profile_home(fleet, "trt") / "skills" / "trt-local" / "SKILL.md").is_file()


def test_fixture_skill_placement_scopes(fleet):
    make_skill(profile_home(fleet, "growth"), "g-owned", owner="trt")
    assert (profile_home(fleet, "growth") / "skills" / "g-owned" / "SKILL.md").is_file()

    make_skill(fleet, "global-bare", owner=None, scope="global")
    assert (fleet / "skills" / "global-bare" / "SKILL.md").is_file()
    assert not (profile_home(fleet, "trt") / "skills" / "global-bare").exists()

    make_skill(fleet, "global-j", owner=None, scope="global", justification="control-plane")
    md = (fleet / "skills" / "global-j" / "SKILL.md").read_text()
    assert "control-plane" in md


def test_snapshot_tree_detects_mutation(fleet):
    make_skill(profile_home(fleet, "trt"), "snap", owner="trt")
    before = snapshot_tree(fleet)
    (profile_home(fleet, "trt") / "skills" / "snap" / "SKILL.md").write_text("tampered")
    after = snapshot_tree(fleet)
    assert before != after


def test_root_skills_dir_resolution(fleet):
    assert root_skills_dir(fleet) == fleet / "skills"
    assert root_skills_dir(profile_home(fleet, "trt")) == fleet / "skills"


def test_routed_content_substitution():
    from qa.contracts import routed_content

    c = routed_content("missing-profile", "ghost-skill")
    assert "owner_profile: missing-profile" in c
    assert "name: ghost-skill" in c
    assert "owner_profile: trt" not in c


def test_runtime_fingerprint_report(core_root, core_commit):
    """Fingerprint surface used by qa/matrix.md run headers (SPEC-3 evidence)."""
    import platform

    assert core_root.is_dir()
    assert (core_root / "hermes_constants.py").is_file()
    print(f"\nCORE={core_commit} ROOT={core_root} PY={platform.python_version()} OS={platform.platform()}")


def test_installed_core_lacks_pr_symbols(core_root):
    """Ground truth anchor: installed core must NOT have e12d79edd1 symbols
    (PR #87101 unmerged) — the dormancy simulation in A7b monkeypatches them
    in, which only works on a core that lacks them natively."""
    import importlib

    smt = importlib.import_module("tools.skill_manager_tool")
    assert not hasattr(smt, "_skill_owner_routing_policy")
    assert not hasattr(smt, "_create_skill_with_owner_routing")


# -- QA gate script invariants (OCR M13/M14) -------------------------------


def test_run_gates_runs_suite_once(core_root):
    """M13: the suite must run exactly once — the old header embedded a
    full pytest run inside echo's command substitution, whose exit status
    hid that run's failure under set -e before the suite ran again."""
    script = (Path(__file__).resolve().parents[2] / "qa" / "run_gates.sh").read_text()
    # exactly one pytest invocation, and it is NOT inside a substitution
    assert '"$VENV_PY" -m pytest' in script
    assert script.count('"$VENV_PY" -m pytest') == 1
    assert '$("$VENV_PY" -m pytest' not in script
