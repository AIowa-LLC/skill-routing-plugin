"""Regression pins: 001eb34 + d4effac engine fixes and the M5 id collision.

Pinned behaviors (board skill-ownership-plugin, card t_414faadf):

- 001eb34 — misplaced-global honors global_justification: a global skill
  carrying owner_profile AND a valid justification (shared-primitive) is a
  legitimate shared primitive and must NOT emit a misplaced-global finding;
  the same owner-carrying global WITHOUT justification still emits (control).
- d4effac — skill-dir symlinks resolving outside the scanned home are
  aliases, not copies: the physical skill is counted exactly once, at its
  canonical home; no phantom duplicate/drift findings are manufactured.
- M5 (known bug, deliberately NOT fixed here — MASTER-REPORT t_79481b53):
  finding ids are digested from kind|scope|skill with no path discriminator,
  so a REAL profiles/default/skills dir mirroring a global skill yields two
  distinct findings that share one id. Pinned as strict xfail so the eventual
  fix flips it to XPASS and forces deliberate marker removal.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import write_skill

JUSTIFIED_FM = "    global_justification: shared-primitive\n"


def run_scan():
    from skill_owner_routing import drift

    return drift.scan()


class TestMisplacedGlobalHonorsJustification:
    """001eb34: owner-carrying global + valid global_justification is clean."""

    def test_justified_owner_carrying_global_is_not_flagged(self, fleet):
        write_skill(fleet["root"], "prim-lib", owner="trt", extra_fm=JUSTIFIED_FM)
        result = run_scan()
        # guard against a vacuous pass: the skill really was scanned
        assert result["scanned"] == 1
        assert not any(f["skill"] == "prim-lib" for f in result["findings"])

    def test_unjustified_owner_carrying_global_still_flagged(self, fleet):
        # control: same shape, no justification -> finding must still fire
        write_skill(fleet["root"], "hoarder", owner="trt")
        result = run_scan()
        finding = next(f for f in result["findings"] if f["skill"] == "hoarder")
        assert finding["kind"] == "misplaced-global"
        assert finding["severity"] == "medium"
        assert finding["expected_owner"] == "trt"
        assert finding["actual"] == "default"
        assert finding["status"] == "open"

    def test_body_marker_justification_honored_for_owner_carrying_global(
        self, fleet
    ):
        skill_dir = fleet["root"] / "skills" / "marked-global"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: marked-global\ndescription: x.\nmetadata:\n  hermes:\n"
            "    owner_profile: trt\n---\n\n"
            "Global justification: shared-primitive\n",
            encoding="utf-8",
        )
        result = run_scan()
        assert result["scanned"] == 1
        assert not any(
            f["skill"] == "marked-global" for f in result["findings"]
        )


def _symlink(target: str, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)


class TestCrossHomeSymlinkAliases:
    """d4effac: a skill-dir symlink resolving outside the scanned home is an
    alias — skipped there, the physical skill counted once at its canonical
    home (top-level, chained, and one-category-nested variants)."""

    def test_aliased_global_counted_once_no_phantom_findings(self, fleet):
        # canonical physical skills
        write_skill(fleet["root"], "shared-prim", extra_fm=JUSTIFIED_FM)
        write_skill(fleet["trt"], "trt-owned", owner="trt")
        write_skill(fleet["growth"], "growth-owned", owner="growth")
        # aliases into other homes, all resolving OUTSIDE those homes:
        # top-level in trt
        _symlink(
            "../../../skills/shared-prim",
            fleet["trt"] / "skills" / "shared-prim",
        )
        # chained in growth: -> trt's alias -> canonical
        _symlink(
            "../../trt/skills/shared-prim",
            fleet["growth"] / "skills" / "shared-prim",
        )
        # nested one category deep in trt
        _symlink(
            "../../../../skills/shared-prim",
            fleet["trt"] / "skills" / "devtools" / "shared-prim",
        )

        result = run_scan()
        # every physical skill counted exactly once — aliases add nothing
        assert result["scanned"] == 3

        from skill_owner_routing import drift

        counts: dict[tuple[str, str], int] = {}
        for scope, home in drift._all_homes():
            for name, _md in drift._skills_in(home):
                counts[(scope, name)] = counts.get((scope, name), 0) + 1
        assert counts == {
            ("default", "shared-prim"): 1,
            ("trt", "trt-owned"): 1,
            ("growth", "growth-owned"): 1,
        }

        # no phantom duplicate/drift manufactured for the single physical copy
        kinds = {f["kind"] for f in result["findings"]}
        assert "duplicate/hoarding" not in kinds
        assert not any(
            f["skill"] == "shared-prim" for f in result["findings"]
        )


class TestFindingIdCollisionDefaultTwin:
    """M5 (fixed in P5): a REAL profiles/default/skills directory mirroring a
    global skill produces two distinct findings (one per physical copy) —
    they must carry DISTINCT ids (digest includes the resolved path)."""

    def test_distinct_twin_findings_have_distinct_ids(self, fleet):
        default_profile = fleet["root"] / "profiles" / "default"
        default_profile.mkdir(parents=True)
        write_skill(fleet["root"], "twin-skill", owner="trt")
        write_skill(default_profile, "twin-skill", owner="trt")

        result = run_scan()
        # both physical copies were scanned...
        assert result["scanned"] == 2
        # ...and each produced its own misplaced-global finding
        twins = [
            f
            for f in result["findings"]
            if f["kind"] == "misplaced-global" and f["skill"] == "twin-skill"
        ]
        assert len(twins) == 2
        # distinct findings (distinct physical paths) => distinct ids
        ids = [f["id"] for f in twins]
        assert len(set(ids)) == 2, f"finding-id collision: {ids}"
