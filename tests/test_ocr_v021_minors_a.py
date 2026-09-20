"""OCR v0.2.1 minors — Group A: gate/engine enforcement integrity.

Covers the index/drift/register/hoarding minors from the triaged report
(MINOR section, enforcement/engine cluster):

- index.lookup race: a merge that lands a fresh entry between the lookup's
  scan and its stale-pop must NOT delete the fresh entry.
- index resolution failures are LOUD: _within_current_fleet /
  _profile_roots log instead of silently shrinking the root set.
- drift._index_entries: duplicate names resolve deterministically
  (first-seen wins — default home first, same as build_index_from_scan)
  and the collision is logged.
- drift._skills_in: within-home symlinked skill dirs are skipped by ONE
  policy at admission (no admitted-then-dropped branch, no spurious
  containment warning).
- register audit tool: unknown action errors instead of falling through
  to a full scan with persistent side effects.
- hoarding: the body-marker alternation is derived from
  JUSTIFICATION_KEYS (every key reachable through the marker).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

from conftest import write_skill

REPO = Path(__file__).resolve().parent.parent


class TestIndexRace:
    def test_merge_landing_during_lookup_is_not_deleted(self, fleet, monkeypatch):
        """index check-then-act race: the drift scan's merge lands a fresh
        entry for this name while our lookup's bounded scan is in flight
        and misses — the stale-pop must not delete the fresh entry."""
        from skill_owner_routing import index

        write_skill(fleet["root"], "raced", owner="trt")
        fresh = str(fleet["root"] / "skills" / "raced" / "SKILL.md")
        stale = str(fleet["root"] / "skills" / "gone" / "SKILL.md")
        index.update_index({"raced": stale})  # stale cache hit

        real_scan = index._bounded_scan

        def scan_that_misses_while_merge_lands(name):
            # The drift scan lands the fresh entry concurrently...
            index.merge_entries({"raced": fresh})
            # ...but OUR scan misses (it raced the skill's creation).
            return real_scan(name) if name != "raced" else None

        monkeypatch.setattr(index, "_bounded_scan", scan_that_misses_while_merge_lands)
        assert index.lookup("raced") is None  # this lookup missed
        with index._LOCK:
            survived = index._INDEX.get("raced")
        assert survived == fresh, "stale-pop deleted the just-merged entry"
        # and with scanning restored, the next lookup resolves through the
        # surviving entry (no re-scan needed)
        monkeypatch.setattr(index, "_bounded_scan", real_scan)
        assert str(index.lookup("raced")) == fresh

    def test_stale_entry_is_still_dropped(self, fleet):
        """The compare-and-delete guard must still drop genuinely stale
        entries (no scan hit, no concurrent merge)."""
        from skill_owner_routing import index

        stale = str(fleet["root"] / "skills" / "gone" / "SKILL.md")
        index.update_index({"gone": stale})
        assert index.lookup("gone") is None
        with index._LOCK:
            assert "gone" not in index._INDEX


class TestLoudIndexDegradation:
    def test_within_current_fleet_failure_logs(self, fleet, monkeypatch, caplog):
        from skill_owner_routing import index

        def broken_home():
            raise RuntimeError("fleet root unresolvable")

        monkeypatch.setattr(
            "skill_owner_routing.common.fleet_default_home", broken_home
        )
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.index"):
            ok = index._within_current_fleet(fleet["root"] / "skills")
        assert ok is False  # containment still fails closed
        assert any("fleet root" in r.message for r in caplog.records)

    def test_profile_roots_failure_logs_partial(self, fleet, monkeypatch, caplog):
        """A DEFAULT-home resolution failure must be named in the log; the
        current home's root is still returned (partial, not empty)."""
        from skill_owner_routing import index

        def broken_default():
            raise RuntimeError("default home unresolvable")

        monkeypatch.setattr(
            "skill_owner_routing.common.fleet_default_home", broken_default
        )
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.index"):
            roots = index._profile_roots()
        assert fleet["root"] / "skills" in roots  # current home survived
        assert any(
            "EXCLUDED" in r.message for r in caplog.records
        ), "silent root-set shrink"


class TestDeterministicIndexBuild:
    def test_duplicate_names_first_seen_wins_with_warning(self, fleet, caplog):
        """Same name global + profile: first-seen (default home, which
        _all_homes yields first) wins — not lexicographic-last — and the
        collision is logged."""
        from skill_owner_routing import drift

        default_copy = str(fleet["root"] / "skills" / "twin" / "SKILL.md")
        profile_copy = str(fleet["growth"] / "skills" / "twin" / "SKILL.md")
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.drift"):
            entries = drift._index_entries(
                [
                    {"skill": "twin", "path": default_copy},
                    {"skill": "twin", "path": profile_copy},
                ]
            )
        assert entries["twin"] == default_copy
        assert any("multiple homes" in r.message for r in caplog.records)

    def test_scan_feeds_deterministic_entries(self, fleet):
        """End-to-end: a real scan of a global+profile duplicate indexes
        the default-home copy (first-seen), deterministically."""
        from skill_owner_routing import drift, index

        write_skill(fleet["root"], "twin", owner="trt")
        write_skill(fleet["growth"], "twin", owner="trt")
        drift.scan()
        with index._LOCK:
            assert index._INDEX["twin"] == str(
                fleet["root"] / "skills" / "twin" / "SKILL.md"
            )
        drift.scan()  # rescans are stable
        with index._LOCK:
            assert index._INDEX["twin"] == str(
                fleet["root"] / "skills" / "twin" / "SKILL.md"
            )


class TestSymlinkPolicyReconciled:
    def test_within_home_symlinked_skill_dir_skipped_quietly(self, fleet, caplog):
        """One policy at admission: within-home symlinked dirs are skipped
        as aliases — never admitted then dropped by _contained_skill_md
        (which fired a spurious containment warning per alias)."""
        from skill_owner_routing import drift

        write_skill(fleet["root"], "real")
        os.symlink("real", fleet["root"] / "skills" / "alias")
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.drift"):
            found = drift._skills_in(fleet["root"])
        names = [name for name, _md in found]
        assert "alias" not in names
        assert "real" in names
        assert not any(
            "containment" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]

    def test_outside_home_symlinked_skill_dir_still_skipped(self, fleet):
        from skill_owner_routing import drift

        write_skill(fleet["trt"], "trt-owned", owner="trt")
        (fleet["root"] / "skills").mkdir(parents=True, exist_ok=True)
        link = fleet["root"] / "skills" / "trt-alias"
        os.symlink(
            os.path.relpath(
                fleet["trt"] / "skills" / "trt-owned", fleet["root"] / "skills"
            ),
            link,
        )
        found = drift._skills_in(fleet["root"])
        assert "trt-alias" not in [name for name, _md in found]

    def test_symlink_regression_suite_still_holds(self, fleet):
        """The d4effac cross-home alias invariants survive the policy
        merge: physical skills counted exactly once, no phantoms."""
        from skill_owner_routing import drift

        write_skill(fleet["root"], "shared-prim")
        write_skill(fleet["trt"], "trt-owned", owner="trt")
        link = fleet["trt"] / "skills" / "shared-prim"
        os.symlink("../../../skills/shared-prim", link)
        result = drift.scan()
        assert result["scanned"] == 2
        kinds = {f["kind"] for f in result["findings"]}
        assert "duplicate/hoarding" not in kinds


class TestAuditActionVocabulary:
    @staticmethod
    def _audit_handler(monkeypatch, tmp_path):
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from skill_owner_routing import common
        from skill_owner_routing.register import register
        from test_register import FakeContext

        common.reset_caches()
        ctx = FakeContext()
        register(ctx)
        return ctx, common

    def test_unknown_action_errors_without_scan(self, tmp_path, monkeypatch):
        """A typo'd/case-variant action must NOT fall through to a full
        scan with persistent side effects (ledger upsert + index rewrite)."""
        ctx, common = self._audit_handler(monkeypatch, tmp_path)
        try:
            handler = ctx.tools["skill_owner_audit"]["handler"]
            for bad in ("rescan", "Scan", "LIST"):
                result = json.loads(handler({"action": bad}))
                assert result["ok"] is False, bad
                assert "Unknown action" in result["error"], bad
                assert "scan" in result["error"] and "list" in result["error"]
            # and no ledger was written by the refused calls
            from skill_owner_routing import ledger

            assert not ledger.ledger_path().exists()
        finally:
            common.reset_caches()

    def test_empty_action_defaults_to_scan(self, tmp_path, monkeypatch):
        ctx, common = self._audit_handler(monkeypatch, tmp_path)
        try:
            result = json.loads(ctx.tools["skill_owner_audit"]["handler"]({}))
            assert result["ok"] is True
            assert result["scanned"] == 0
        finally:
            common.reset_caches()


class TestJustificationDerivation:
    def test_every_key_reachable_through_body_marker(self):
        """The alternation is derived from the set — every key must match
        through the body marker (both spellings); a bogus one must not."""
        from skill_owner_routing.hoarding import (
            JUSTIFICATION_KEYS,
            global_justification,
        )

        assert len(JUSTIFICATION_KEYS) == 3
        for key in JUSTIFICATION_KEYS:
            assert global_justification({}, f"global justification: {key}\n") == key
            assert global_justification({}, f"global-justification: {key}\n") == key
        assert global_justification({}, "global justification: vibes\n") is None
