"""Drift watchdog + hoarding lint + ledger tests (SPEC-3 Gate B).

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

import builtins
import json

from conftest import write_skill


def run_scan():
    from skill_owner_routing import drift

    return drift.scan()


class TestDriftScan:
    def test_clean_fleet_produces_no_open_findings(self, fleet):
        write_skill(fleet["trt"], "trt-skill", owner="trt")
        write_skill(fleet["root"], "core-skill")  # unowned but... see hoarding
        result = run_scan()
        kinds = {(f["kind"], f["skill"]) for f in result["findings"]}
        # core-skill has no owner and no justification → hoarding finding
        assert ("unowned", "core-skill") in kinds
        assert ("drifted", "trt-skill") not in kinds

    def test_drifted_skill_detected(self, fleet):
        # B1: skill in wrong profile vs owner_profile
        write_skill(fleet["growth"], "misplaced", owner="trt")
        result = run_scan()
        finding = next(f for f in result["findings"] if f["skill"] == "misplaced")
        assert finding["kind"] == "drifted"
        assert finding["severity"] == "high"
        assert finding["expected_owner"] == "trt"
        assert finding["actual"] == "growth"
        assert finding["status"] == "open"

    def test_misplaced_global_detected(self, fleet):
        # B1b: global skill w/ owner_profile set
        write_skill(fleet["root"], "hoarder", owner="trt")
        result = run_scan()
        finding = next(f for f in result["findings"] if f["skill"] == "hoarder")
        assert finding["kind"] == "misplaced-global"

    def test_unknown_owner_detected(self, fleet):
        # B2: owner id not a registered profile
        write_skill(fleet["trt"], "ghost-owned", owner="missing-profile")
        result = run_scan()
        finding = next(f for f in result["findings"] if f["skill"] == "ghost-owned")
        assert finding["kind"] == "unknown-owner"

    def test_duplicate_global_and_profile_detected(self, fleet):
        # B2b: global copy + profile copy both exist
        write_skill(fleet["root"], "twin")
        write_skill(fleet["trt"], "twin")
        result = run_scan()
        dupes = [f for f in result["findings"] if f["kind"] == "duplicate/hoarding"]
        assert any(f["skill"] == "twin" for f in dupes)

    def test_category_nested_skills_discovered(self, fleet):
        content = (
            "---\nname: nested\ndescription: x.\nmetadata:\n  hermes:\n"
            "    owner_profile: trt\n---\n\nBody\n"
        )
        skill_dir = fleet["growth"] / "skills" / "devops" / "nested"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        result = run_scan()
        assert any(f["skill"] == "nested" for f in result["findings"])

    def test_findings_use_canonical_shape(self, fleet):
        # SPEC-0 Interface 3: BINDING record shape + kind enum
        write_skill(fleet["growth"], "misplaced", owner="trt")
        result = run_scan()
        finding = next(f for f in result["findings"] if f["skill"] == "misplaced")
        assert set(finding.keys()) == {
            "id", "kind", "severity", "skill", "expected_owner",
            "actual", "proposed_fix", "discovered_at", "status",
        }
        valid_kinds = {
            "drifted", "misplaced-global", "unknown-owner",
            "duplicate/hoarding", "unowned",
        }
        for f in result["findings"]:
            assert f["kind"] in valid_kinds

    def test_deterministic_ids_across_rescans(self, fleet):
        # D3: two audits of same state → identical findings (ex timestamps)
        write_skill(fleet["growth"], "misplaced", owner="trt")
        first = run_scan()["findings"]
        second = run_scan()["findings"]
        strip = lambda fs: sorted(
            (f["id"], f["kind"], f["skill"], f["actual"]) for f in fs
        )
        assert strip(first) == strip(second)

    def test_rescan_is_idempotent_in_ledger(self, fleet):
        write_skill(fleet["growth"], "misplaced", owner="trt")
        run_scan()
        run_scan()
        from skill_owner_routing import ledger

        ids = [f["id"] for f in ledger.load_findings()]
        assert len(ids) == len(set(ids))

    def test_scan_updates_mutation_index(self, fleet):
        write_skill(fleet["trt"], "indexed-skill", owner="trt")
        run_scan()
        from skill_owner_routing import index

        found = index.lookup("indexed-skill")
        assert found is not None and found.parent.name == "indexed-skill"

    def test_scan_performance_500_skills(self, fleet):
        # B3: N=500 completes <10s
        import time

        for i in range(500):
            write_skill(fleet["trt"], f"bulk-{i}", owner="trt")
        start = time.perf_counter()
        result = run_scan()
        elapsed = time.perf_counter() - start
        assert result["scanned"] >= 500
        assert elapsed < 10.0, f"scan took {elapsed:.1f}s"


class TestHoardingLint:
    def test_unjustified_global_flagged(self, fleet):
        write_skill(fleet["root"], "loose-skill")
        result = run_scan()
        assert any(
            f["kind"] == "unowned" and f["skill"] == "loose-skill"
            for f in result["findings"]
        )

    def test_control_plane_justification_clean(self, fleet):
        write_skill(
            fleet["root"],
            "fleet-cli",
            extra_fm="    global_justification: control-plane\n",
        )
        result = run_scan()
        assert not any(f["skill"] == "fleet-cli" for f in result["findings"])

    def test_shared_primitive_justification_clean(self, fleet):
        write_skill(
            fleet["root"],
            "shared-lib",
            extra_fm="    global_justification: shared-primitive\n",
        )
        result = run_scan()
        assert not any(f["skill"] == "shared-lib" for f in result["findings"])

    def test_structural_dependency_justification_clean(self, fleet):
        write_skill(
            fleet["root"],
            "structural",
            extra_fm="    global_justification: verified-structural-dependency\n",
        )
        result = run_scan()
        assert not any(f["skill"] == "structural" for f in result["findings"])

    def test_bogus_justification_flagged(self, fleet):
        write_skill(
            fleet["root"], "bogus", extra_fm="    global_justification: vibes\n"
        )
        result = run_scan()
        assert any(
            f["kind"] == "unowned" and f["skill"] == "bogus"
            for f in result["findings"]
        )

    def test_suffixed_marker_not_accepted(self, fleet):
        # M9 (OCR review @ d1659fb): the body-marker regex had no trailing
        # boundary, so "control-plane-legacy" / "shared-primitive-v2" /
        # "control-planeX" matched their prefix and silently passed the
        # hoarding lint. (The plural y→ies case does NOT match — do not
        # use it.) Probed red on base via hoarding.global_justification.
        from skill_owner_routing.hoarding import global_justification

        for bad_body in [
            "global justification: control-plane-legacy\n",
            "global justification: shared-primitive-v2\n",
            "global justification: control-planeX\n",
        ]:
            assert global_justification({}, bad_body) is None, bad_body
        # exact keys still pass
        assert global_justification({}, "global justification: control-plane\n") == "control-plane"
        assert global_justification({}, "global-justification: shared-primitive\n") == "shared-primitive"
        # and the same suffixed markers in a real scan produce findings
        for i, marker in enumerate(
            ["control-plane-legacy", "shared-primitive-v2", "control-planeX"]
        ):
            skill_dir = fleet["root"] / "skills" / f"suffixed-{i}"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: suffixed-{i}\ndescription: x.\n---\n\n"
                f"global justification: {marker}\n",
                encoding="utf-8",
            )
        result = run_scan()
        flagged = {f["skill"] for f in result["findings"] if f["kind"] == "unowned"}
        assert {"suffixed-0", "suffixed-1", "suffixed-2"} <= flagged

    def test_body_marker_justification_accepted(self, fleet):
        skill_dir = fleet["root"] / "skills" / "marked"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: marked\ndescription: x.\n---\n\n"
            "Global justification: shared-primitive\n",
            encoding="utf-8",
        )
        result = run_scan()
        assert not any(f["skill"] == "marked" for f in result["findings"])


class TestLedger:
    def test_ledger_lands_in_default_home(self, fleet):
        write_skill(fleet["growth"], "misplaced", owner="trt")
        run_scan()
        ledger_path = fleet["root"] / "skills" / ".skill_owner_findings.json"
        assert ledger_path.is_file()
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert any(f["skill"] == "misplaced" for f in data["findings"])

    def test_resolved_status_survives_rescan(self, fleet):
        write_skill(fleet["growth"], "misplaced", owner="trt")
        run_scan()
        from skill_owner_routing import ledger

        target = next(
            f for f in ledger.load_findings() if f["skill"] == "misplaced"
        )
        ledger.update_status(target["id"], "resolved")
        run_scan()  # drift still present
        after = next(
            f for f in ledger.load_findings() if f["skill"] == "misplaced"
        )
        assert after["status"] == "resolved"

    def test_fixed_drift_leaves_ledger(self, fleet):
        from conftest import write_skill as ws

        skill_md = ws(fleet["growth"], "fixed", owner="trt")
        run_scan()
        skill_md.unlink()  # drift removed
        run_scan()
        from skill_owner_routing import ledger

        assert not any(f["skill"] == "fixed" for f in ledger.load_findings())

    def test_audit_tool_returns_json(self, fleet):
        write_skill(fleet["growth"], "misplaced", owner="trt")
        from skill_owner_routing.drift import run_audit

        result = json.loads(run_audit())
        assert result["ok"] is True
        assert result["counts"]["open"] >= 1
        assert "run_id" in result


class TestLedgerFailLoud:
    """M5 (OCR review @ d1659fb): load failure must never silently map to
    [] — the next save would wipe resolved/acknowledged audit history."""

    def test_corrupt_ledger_raises_on_load_and_survives_scan(self, fleet):
        import pytest

        from skill_owner_routing import ledger

        path = fleet["root"] / "skills" / ".skill_owner_findings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")

        with pytest.raises(ledger.LedgerError):
            ledger.load_findings()
        # the audit path surfaces the failure (ok:false), it does NOT
        # swallow it and overwrite the file with a fresh scan
        from skill_owner_routing.drift import run_audit

        result = json.loads(run_audit())
        assert result["ok"] is False
        assert path.read_text(encoding="utf-8") == "{ this is not json"

    def test_unreadable_ledger_raises_and_blocks_save(self, fleet, monkeypatch):
        import pytest

        from skill_owner_routing import ledger

        path = fleet["root"] / "skills" / ".skill_owner_findings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"findings": []}', encoding="utf-8")

        def boom(*args, **kwargs):
            raise PermissionError("transient read blip")

        monkeypatch.setattr(ledger, "ledger_path", lambda: path)
        monkeypatch.setattr(
            "skill_owner_routing.ledger.Path.read_text", boom
        )
        with pytest.raises(ledger.LedgerError):
            ledger.load_findings()

        finding = {
            "id": "unowned-x",
            "kind": "unowned",
            "severity": "low",
            "skill": "x",
            "expected_owner": None,
            "actual": "default",
            "proposed_fix": "fix",
            "discovered_at": "2026-01-01T00:00:00Z",
            "status": "open",
        }
        with pytest.raises(ledger.LedgerError):
            ledger.upsert_findings([finding])
        monkeypatch.undo()  # restore real read_text for the verification
        assert path.read_text(encoding="utf-8") == '{"findings": []}'

    def test_invalid_records_raise(self, fleet):
        import pytest

        from skill_owner_routing import ledger

        path = fleet["root"] / "skills" / ".skill_owner_findings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"findings": [{"garbage": true}, {"id": "ok", "kind": "unowned", "skill": "s"}]}',
            encoding="utf-8",
        )
        with pytest.raises(ledger.LedgerError):
            ledger.load_findings()

    def test_missing_ledger_still_returns_empty(self, fleet):
        # no file yet (first ever scan) is NOT a failure
        from skill_owner_routing import ledger

        assert ledger.load_findings() == []

    def test_list_action_reports_ledger_error(self, fleet):
        # register.py's audit tool 'list' action must surface the failure
        # as a JSON error, not an empty findings list
        from skill_owner_routing.register import register

        path = fleet["root"] / "skills" / ".skill_owner_findings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"findings": [', encoding="utf-8")

        captured = {}

        class Ctx:
            def register_hook(self, *a, **k):
                pass

            def register_tool(self, name, handler, **k):
                captured[name] = handler

        register(Ctx())
        result = json.loads(captured["skill_owner_audit"]({"action": "list"}))
        assert result["ok"] is False
        assert "ledger" in result["error"].lower()


class TestCoexistence:
    def test_dormancy_when_core_enforces(self, fleet, enabled_config, monkeypatch):
        # A7b/D5: core symbol + core enabled → create gate DORMANT
        import tools.skill_manager_tool as smt

        monkeypatch.setattr(
            smt, "_skill_owner_routing_policy", lambda: {"enabled": True}, raising=False
        )
        from conftest import OWNER_ROUTED_SKILL_CONTENT
        from skill_owner_routing import common
        from skill_owner_routing.gate import pre_tool_call

        common.reset_caches()
        result = pre_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": OWNER_ROUTED_SKILL_CONTENT},
        )
        assert result is None  # dormant: no deny from plugin

    def test_no_dormancy_when_core_symbol_absent(self, fleet, enabled_config):
        # Current core (no #87101) → gate stays active
        import tools.skill_manager_tool as smt

        assert not hasattr(smt, "_skill_owner_routing_policy") or _core_disabled()
        from conftest import OWNER_ROUTED_SKILL_CONTENT
        from skill_owner_routing.gate import pre_tool_call

        result = pre_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": OWNER_ROUTED_SKILL_CONTENT},
        )
        assert result is not None and result["action"] == "block"

    def test_partial_core_policy_disabled_stays_active(self, fleet, enabled_config, monkeypatch):
        # D5b: symbol present but core policy disabled → plugin active
        import tools.skill_manager_tool as smt

        monkeypatch.setattr(
            smt, "_skill_owner_routing_policy", lambda: {"enabled": False}, raising=False
        )
        from conftest import OWNER_ROUTED_SKILL_CONTENT
        from skill_owner_routing import common, coexistence
        from skill_owner_routing.gate import pre_tool_call

        common.reset_caches()
        assert coexistence.create_gate_dormant() is False
        result = pre_tool_call(
            tool_name="skill_manage",
            args={"action": "create", "name": "x", "content": OWNER_ROUTED_SKILL_CONTENT},
        )
        assert result is not None and result["action"] == "block"

    def test_dormancy_reactivates_after_core_removed(self, fleet, enabled_config, monkeypatch):
        # D5c: no zombie dormancy cache
        import tools.skill_manager_tool as smt

        monkeypatch.setattr(
            smt, "_skill_owner_routing_policy", lambda: {"enabled": True}, raising=False
        )
        from skill_owner_routing import common, coexistence

        common.reset_caches()
        assert coexistence.create_gate_dormant() is True
        monkeypatch.delattr(smt, "_skill_owner_routing_policy", raising=False)
        # probe cache is time-boxed; force expiry
        from skill_owner_routing import coexistence as co

        co._CORE_PROBE_CACHE.clear()
        assert coexistence.create_gate_dormant() is False

    def test_dormancy_logs_once(self, fleet, enabled_config, monkeypatch):
        import tools.skill_manager_tool as smt

        monkeypatch.setattr(
            smt, "_skill_owner_routing_policy", lambda: {"enabled": True}, raising=False
        )
        from skill_owner_routing import coexistence

        coexistence._DORMANT_STATE.clear()
        coexistence._CORE_PROBE_CACHE.clear()
        assert coexistence.mark_dormancy_logged() is True
        assert coexistence.mark_dormancy_logged() is False


def _core_disabled() -> bool:
    try:
        import tools.skill_manager_tool as smt

        policy = smt._skill_owner_routing_policy()
        return not policy.get("enabled")
    except Exception:
        return True


class TestParserFailureLoud:
    """M8 — parser-import failure must fail the scan, never silently
    reclassify the fleet ownerless.

    Old behavior: _read() caught the import AND the parse in one except and
    degraded every skill to empty frontmatter with zero logging — drift
    findings suppressed, spurious hoarding lints, run_audit still
    ok:true. New contract: import failure raises (watchdog malfunction);
    a single skill's parse failure degrades that one skill only, logged.
    """

    def test_parser_import_failure_raises_not_silent_ownerless(
        self, fleet, monkeypatch, caplog
    ):
        import logging

        import pytest

        from skill_owner_routing import drift

        write_skill(fleet["trt"], "trt-skill", owner="trt")

        real_import = builtins.__import__

        def broken_import(name, *a, **k):
            if name == "agent.skill_utils":
                raise ImportError("parser gone")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", broken_import)
        with caplog.at_level(logging.ERROR, logger="skill_owner_routing.drift"):
            with pytest.raises(RuntimeError, match="frontmatter parser unavailable"):
                drift.scan()
        assert any("parser unavailable" in r.message for r in caplog.records)

    def test_single_skill_parse_failure_degrades_that_skill_only(
        self, fleet, monkeypatch, caplog
    ):
        import logging

        import agent.skill_utils as su
        from skill_owner_routing import drift

        # good-skill is MISPLACED (owner trt, lives in growth) so its drift
        # finding proves classification survived the sibling's crash.
        write_skill(fleet["growth"], "good-skill", owner="trt")
        bad = fleet["root"] / "skills" / "bad-skill"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "SKILL.md").write_text(
            "---\nname: bad-skill\n---\n\nbody\n", encoding="utf-8"
        )
        real_parse = su.parse_frontmatter

        def parse_that_blows_up_on_bad(content):
            if "bad-skill" in content:
                raise ValueError("simulated parser crash")
            return real_parse(content)

        monkeypatch.setattr(su, "parse_frontmatter", parse_that_blows_up_on_bad)
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.drift"):
            result = drift.scan()
        # Scan completes; both skills scanned; the crash degraded ONLY the
        # bad skill (logged, named), the good skill keeps its drift finding.
        assert result["scanned"] == 2
        assert any(
            "parse failed for" in r.message and "bad-skill" in r.getMessage()
            for r in caplog.records
        )
        assert any(f["skill"] == "good-skill" and f["kind"] == "drifted"
                   for f in result["findings"])


class TestContainmentWarningRateLimited:
    """OCR minor (drift.py:195-202) — _warn_skip's "bounded warning" list
    never gated the logging: every containment skip logged, flooding on a
    fleet with many symlinked/escaping paths. The warning is now
    rate-limited; the bounded list survives for diagnostics."""

    def test_repeated_skips_log_once_per_window(self, caplog):
        import logging

        from skill_owner_routing import drift

        drift._skip_warn_last[0] = 0.0  # window open
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.drift"):
            drift._warn_skip()
            drift._warn_skip()
            drift._warn_skip()
        warnings = [r for r in caplog.records if "containment" in r.getMessage()]
        assert len(warnings) == 1  # flood gated: 3 skips, 1 log line

    def test_new_window_logs_again(self, caplog, monkeypatch):
        import logging

        from skill_owner_routing import drift

        drift._skip_warn_last[0] = 0.0
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.drift"):
            drift._warn_skip()
        assert len([r for r in caplog.records if "containment" in r.getMessage()]) == 1
        # jump past the window
        monkeypatch.setattr(drift._time_marker, "monotonic", lambda: 10_000.0) if hasattr(
            drift, "_time_marker"
        ) else None
        drift._skip_warn_last[0] = -drift._SKIP_WARN_RATE  # force window elapsed
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.drift"):
            drift._warn_skip()
        assert len([r for r in caplog.records if "containment" in r.getMessage()]) == 2
