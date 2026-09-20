"""OCR v0.2.1 minors — Group B: ledger integrity (M5 family).

- upsert_findings stamps status itself (no caller convention: a
  status-less record silently vanished from counts and the preserve
  branch).
- save_findings validates through _valid() before writing (a record the
  loader would reject must never be persisted — persist-then-lose).
- update_status validates the status vocabulary ('RESOLVED' made a
  finding invisible everywhere while the next scan reverted it).
- load→mutate→save is serialized under the module lock (concurrent
  upsert + update_status never interleave half-written state).
"""

from __future__ import annotations

import json
import threading

import pytest

from conftest import write_skill


def _finding(skill="b-skill", kind="drifted", **over):
    from skill_owner_routing.common import new_finding_id, utc_now_iso

    base = {
        "id": new_finding_id(kind, skill, "growth"),
        "kind": kind,
        "severity": "high",
        "skill": skill,
        "expected_owner": "trt",
        "actual": "growth",
        "proposed_fix": "move it",
        "discovered_at": utc_now_iso(),
        "status": "open",
    }
    base.update(over)
    return base


class TestUpsertStampsStatus:
    def test_statusless_new_finding_gets_open(self, fleet):
        """The scan path (drift) always stamps status, but upsert is a
        public surface — a status-less record must be stamped here, not
        trusted to caller convention."""
        from skill_owner_routing import ledger

        raw = _finding()
        del raw["status"]
        counts = ledger.upsert_findings([raw])
        assert counts == {"total": 1, "open": 1}
        saved = ledger.load_findings()[0]
        assert saved["status"] == "open"

    def test_statusless_new_finding_survives_rescan_preserve_branch(self, fleet):
        """A status-less record used to vanish from the preserve branch:
        resolve it, rescan with a status-less input — the resolution
        must survive."""
        from skill_owner_routing import ledger

        first = _finding()
        ledger.upsert_findings([first])
        ledger.update_status(first["id"], "resolved")
        raw = _finding()  # same deterministic id
        del raw["status"]
        ledger.upsert_findings([raw])
        saved = {f["id"]: f for f in ledger.load_findings()}
        assert saved[first["id"]]["status"] == "resolved"


class TestSaveValidates:
    def test_save_rejects_invalid_records(self, fleet):
        """save_findings is the write path — an invalid record must raise
        LedgerError BEFORE touching disk (persist-then-lose)."""
        from skill_owner_routing import ledger

        good = _finding()
        ledger.upsert_findings([good])
        before = ledger.ledger_path().read_text(encoding="utf-8")
        bad = _finding(skill="bad")
        bad["kind"] = "not-a-kind"
        with pytest.raises(ledger.LedgerError, match="invalid finding record"):
            ledger.save_findings([bad])
        # untouched
        assert ledger.ledger_path().read_text(encoding="utf-8") == before

    def test_upsert_rejects_invalid_records_before_write(self, fleet):
        from skill_owner_routing import ledger

        bad = _finding(skill="bad", kind="bogus-kind")
        with pytest.raises(ledger.LedgerError):
            ledger.upsert_findings([bad])
        assert not ledger.ledger_path().exists()


class TestStatusVocabulary:
    def test_update_status_rejects_typos(self, fleet):
        from skill_owner_routing import ledger

        first = _finding()
        ledger.upsert_findings([first])
        for bad in ("RESOLVED", "Resolved", "fixed", ""):
            with pytest.raises(ValueError, match="Invalid finding status"):
                ledger.update_status(first["id"], bad)
        # and the ledger was not corrupted by the refused writes
        assert ledger.load_findings()[0]["status"] == "open"

    def test_valid_statuses_accepted(self, fleet):
        from skill_owner_routing import ledger

        first = _finding()
        ledger.upsert_findings([first])
        for status in ("acknowledged", "resolved", "open"):
            updated = ledger.update_status(first["id"], status)
            assert updated["status"] == status
        assert ledger.load_findings()[0]["status"] == "open"


class TestSerializedWrites:
    def test_stale_upsert_cannot_clobber_a_resolve(self, fleet, monkeypatch):
        """The lost-update interleaving, forced deterministically:

        upsert loads the ledger (finding: open), then blocks before its
        save; update_status resolves the finding and saves; upsert's
        stale copy then lands. WITHOUT the module lock the stale save
        clobbers the resolution (finding back to 'open', resolution
        silently lost). WITH the lock, update_status cannot enter the
        critical section until upsert's save completes — the resolution
        is ordered after and survives.
        """
        from skill_owner_routing import ledger

        seeded = _finding()
        ledger.upsert_findings([seeded])

        b_loaded = threading.Event()
        release_b = threading.Event()
        orig_load = ledger.load_findings
        orig_save = ledger.save_findings

        def hooked_load():
            out = orig_load()
            if threading.current_thread().name == "B":
                b_loaded.set()
            return out

        def hooked_save(findings):
            if threading.current_thread().name == "B":
                # window between upsert's load and its save
                release_b.wait(timeout=5.0)
            return orig_save(findings)

        monkeypatch.setattr(ledger, "load_findings", hooked_load)
        monkeypatch.setattr(ledger, "save_findings", hooked_save)

        def run_b():
            # fresh open copy of the same finding (same deterministic id)
            ledger.upsert_findings([_finding()])

        b = threading.Thread(target=run_b, name="B")
        b.start()
        assert b_loaded.wait(timeout=5.0), "upsert never reached its load"
        # timer release: never depends on update_status completing (that
        # would deadlock when the lock correctly serializes the two)
        timer = threading.Timer(0.3, release_b.set)
        timer.start()
        updated = ledger.update_status(seeded["id"], "resolved")
        b.join(timeout=5.0)
        timer.cancel()
        assert not b.is_alive()
        assert updated is not None and updated["status"] == "resolved"
        final = {f["id"]: f for f in orig_load()}
        assert final[seeded["id"]]["status"] == "resolved", (
            "a stale concurrent write clobbered the resolution"
        )
