"""Regression: default profile symlinks skills/ onto the global skills dir.

The default profile conventionally symlinks ``profiles/default/skills ->
../../skills`` (global skills ARE default's skills). The scanner must treat
that profile as the same home, not a second one — otherwise every global
skill is double-counted and phantom duplicate/drift findings are
manufactured for single physical files.

Found live on the GEEKOM fleet install 2026-08-24 (173-finding audit where
~half were phantom default-scoped twins).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from skill_owner_routing import drift


def _make_skill(root: Path, name: str, owner: str | None, category: str | None = None) -> None:
    d = root / "skills"
    if category:
        d = d / category
    skill_dir = d / name
    skill_dir.mkdir(parents=True)
    meta = ""
    if owner:
        meta = (
            "metadata:\n"
            "  hermes:\n"
            f"    owner_profile: {owner}\n"
        )
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill {name}\n{meta}---\n\nbody\n",
        encoding="utf-8",
    )


def test_default_profile_symlinked_skills_dir_is_not_a_second_home(tmp_path, monkeypatch):
    fleet = tmp_path / "fleet"
    (fleet / "profiles" / "default").mkdir(parents=True)
    (fleet / "profiles" / "ops").mkdir(parents=True)
    # global skills dir + default profile symlinked onto it
    _make_skill(fleet, "global-skill", None)
    _make_skill(fleet, "ops-owned-skill", "ops")
    os.symlink("../../skills", fleet / "profiles" / "default" / "skills")
    # a REAL second profile with its own skills
    _make_skill(fleet / "profiles" / "ops", "ops-local-skill", "ops", category="operations")

    monkeypatch.setenv("HERMES_HOME", str(fleet))

    homes = drift._all_homes()
    scopes = [scope for scope, _ in homes]
    assert scopes == ["default", "ops"], f"symlinked default must be skipped, got {scopes}"

    result = drift.scan()
    skills_seen = set()
    for entry_catalog in [c["skill"] for c in []]:
        pass
    # collect scanned names via findings-free path: rescan catalog
    names = set()
    for scope, home in homes:
        for name, _md in drift._skills_in(home):
            names.add((scope, name))
    # each physical skill appears exactly once
    assert ("default", "global-skill") in names
    assert ("default", "ops-owned-skill") in names
    assert ("ops", "ops-local-skill") in names
    assert ("default", "ops-local-skill") not in names  # no phantom twin

    # and the phantom-duplicate class of findings is absent
    kinds = {f["kind"] for f in result["findings"]}
    assert "duplicate/hoarding" not in kinds
    # ops-owned skill living globally is misplaced-global (medium), not drifted
    assert "drifted" not in kinds
    assert "misplaced-global" in kinds  # ops-owned-skill still sits globally
