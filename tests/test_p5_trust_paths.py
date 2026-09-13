"""P5 trust-path repairs — regression pins for M4, M5, M6, M10 (+ M8 in
tests/desktop-plugin-contract.mjs).

- M4: a chmod-000 directory inside a scanned home must not kill the fleet
  watchdog (watchdog_main completes; readable skills are still reported).
- M5: finding-id digest includes the resolved path — twin findings get
  distinct ids (pinned un-xfailed in test_drift_regressions.py) AND the
  id stays stable across rescans for a non-twin finding (ledger dedupe
  contract), AND resolving one twin leaves the other open.
- M6: when the drift ledger write fails, POST /drift/{id}/resolve returns
  5xx naming the exception class and records NO resolution event; the
  happy path still returns 200 with the event.
- M10: unexpected gate exceptions block fail-closed with a gate-error
  message clearly distinct from a policy violation; policy violations
  keep their normal message; the full traceback is still logged.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

from conftest import OWNER_ROUTED_SKILL_CONTENT, write_skill

REPO = Path(__file__).resolve().parents[1]


def run_scan():
    from skill_owner_routing import drift

    return drift.scan()


# ---------------------------------------------------------------------------
# M4 — watchdog crash-resistance: unreadable dirs are skipped, not fatal
# ---------------------------------------------------------------------------

RootOnly = pytest.mark.skipif(
    os.geteuid() == 0, reason="chmod-000 does not block reads for root"
)


class TestWatchdogUnreadableDir:
    @RootOnly
    def test_chmod000_skill_dir_skipped_not_fatal(self, fleet, capsys):
        from skill_owner_routing import drift

        write_skill(fleet["root"], "hoarder", owner="trt")  # misplaced-global
        write_skill(fleet["trt"], "trt-owned", owner="trt")  # clean
        locked = fleet["root"] / "skills" / "locked-skill"
        locked.mkdir(parents=True)
        (locked / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        try:
            os.chmod(locked, 0o000)
            rc = drift.watchdog_main()  # OLD code: PermissionError kills it
        finally:
            os.chmod(locked, 0o755)

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        # the unreadable skill was skipped, not scanned — and not invented
        assert payload["scanned"] == 2
        # other skills are still reported: the hoarder finding survives
        assert payload["open_findings"] >= 1
        assert any("hoarder" in line for line in payload["summary"])

    @RootOnly
    def test_chmod000_nested_grandchild_skipped_not_fatal(self, fleet, capsys):
        from skill_owner_routing import drift

        write_skill(fleet["root"], "hoarder", owner="trt")
        good_cat = fleet["root"] / "skills" / "devtools"
        (good_cat / "good-nested" / "SKILL.md").parent.mkdir(parents=True)
        (good_cat / "good-nested" / "SKILL.md").write_text(
            "---\nname: good-nested\n---\n", encoding="utf-8"
        )
        locked_cat = fleet["root"] / "skills" / "locked-cat"
        (locked_cat / "hidden" / "SKILL.md").parent.mkdir(parents=True)
        (locked_cat / "hidden" / "SKILL.md").write_text(
            "---\nname: hidden\n---\n", encoding="utf-8"
        )
        try:
            os.chmod(locked_cat, 0o000)
            rc = drift.watchdog_main()
        finally:
            os.chmod(locked_cat, 0o755)

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["scanned"] == 2  # hoarder + good-nested; hidden skipped
        assert any("hoarder" in line for line in payload["summary"])


# ---------------------------------------------------------------------------
# M5 — digest path discrimination + id stability
# ---------------------------------------------------------------------------


class TestFindingIdPathDiscrimination:
    def test_id_stable_across_rescans_same_path(self, fleet):
        """Non-twin findings keep ONE id across rescans (ledger dedupe)."""
        write_skill(fleet["growth"], "misplaced", owner="trt")
        first = run_scan()
        second = run_scan()
        ids_a = {f["id"] for f in first["findings"] if f["skill"] == "misplaced"}
        ids_b = {f["id"] for f in second["findings"] if f["skill"] == "misplaced"}
        assert len(ids_a) == 1 and ids_a == ids_b

    def test_digest_changes_when_path_differs(self, tmp_path):
        from skill_owner_routing.common import new_finding_id

        a = tmp_path / "home-a" / "skills" / "twin" / "SKILL.md"
        b = tmp_path / "home-b" / "skills" / "twin" / "SKILL.md"
        id_a = new_finding_id("misplaced-global", "twin", "default", str(a))
        id_b = new_finding_id("misplaced-global", "twin", "default", str(b))
        same = new_finding_id("misplaced-global", "twin", "default", str(a))
        assert id_a != id_b
        assert id_a == same  # deterministic for the identical path

    def test_resolving_one_twin_leaves_other_open(self, fleet):
        """The ledger consequence of distinct twin ids: resolving one must
        not resolve the other (the pre-P5 shared id made both resolve)."""
        default_profile = fleet["root"] / "profiles" / "default"
        default_profile.mkdir(parents=True)
        write_skill(fleet["root"], "twin-skill", owner="trt")
        write_skill(default_profile, "twin-skill", owner="trt")

        result = run_scan()
        twins = [
            f
            for f in result["findings"]
            if f["kind"] == "misplaced-global" and f["skill"] == "twin-skill"
        ]
        assert len(twins) == 2
        ids = sorted(f["id"] for f in twins)
        assert ids[0] != ids[1]

        from skill_owner_routing import ledger

        ledger.update_status(ids[0], "resolved")
        run_scan()  # drift still present on both copies
        after = {
            f["id"]: f["status"]
            for f in ledger.load_findings()
            if f["skill"] == "twin-skill"
        }
        assert after[ids[0]] == "resolved"
        assert after[ids[1]] == "open"  # sibling twin untouched


# ---------------------------------------------------------------------------
# M10 — gate fail-closed on unexpected errors
# ---------------------------------------------------------------------------


def call_gate(action, name="routed-skill", content=None, **extra):
    from skill_owner_routing.gate import pre_tool_call

    args = {"action": action, "name": name}
    if content is not None:
        args["content"] = content
    args.update(extra)
    return pre_tool_call(tool_name="skill_manage", args=args)


class TestGateFailClosed:
    def test_policy_violation_keeps_normal_message(self, fleet, enabled_config):
        """Control: an ordinary policy denial is NOT labelled a gate error."""
        result = call_gate("create", content=OWNER_ROUTED_SKILL_CONTENT)
        assert result is not None and result["action"] == "block"
        assert "skill_owner_create" in result["message"]  # normal redirect text
        assert "gate error" not in result["message"]
        assert "fail-closed" not in result["message"]

    def test_create_path_error_blocks_fail_closed(
        self, fleet, enabled_config, monkeypatch, caplog
    ):
        from skill_owner_routing import gate

        def corrupt(*args, **kwargs):
            raise ValueError("corrupt frontmatter payload")

        monkeypatch.setattr(gate, "_gate_create", corrupt)
        with caplog.at_level(logging.ERROR, logger="skill_owner_routing.gate"):
            result = call_gate("create", content=OWNER_ROUTED_SKILL_CONTENT)

        assert result is not None and result["action"] == "block"  # never silence
        msg = result["message"]
        assert "fail-closed" in msg
        assert "ValueError" in msg  # exception class named
        assert "not a policy violation" in msg  # distinct from policy blocks
        assert "retry" in msg and "report" in msg
        # full traceback still logged
        assert any(record.exc_info for record in caplog.records)

    def test_mutation_path_error_blocks_fail_closed(
        self, fleet, enabled_config, monkeypatch
    ):
        from skill_owner_routing import gate

        def corrupt(*args, **kwargs):
            raise KeyError("index corrupt")

        monkeypatch.setattr(gate, "_gate_mutation", corrupt)
        result = call_gate("edit", name="some-skill", content="x")
        assert result is not None and result["action"] == "block"
        assert "fail-closed" in result["message"]
        assert "KeyError" in result["message"]

    def test_early_bail_unaffected_by_gate_errors(
        self, fleet, enabled_config, monkeypatch
    ):
        """Non-skill_manage tools never reach the decision engine at all."""
        from skill_owner_routing import gate

        def corrupt(*args, **kwargs):
            raise RuntimeError("must never run")

        monkeypatch.setattr(gate, "_decide", corrupt)
        assert gate.pre_tool_call(tool_name="terminal", args={"cmd": "ls"}) is None

    def test_clean_call_still_allowed(self, fleet, enabled_config, monkeypatch):
        """Control: no injected fault → owner-matching create passes."""
        from conftest import set_active_profile

        set_active_profile(monkeypatch, fleet, "trt")
        assert call_gate("create", content=OWNER_ROUTED_SKILL_CONTENT) is None


# ---------------------------------------------------------------------------
# M6 — resolve endpoint: no false success on ledger write failure
# ---------------------------------------------------------------------------

sys.path.insert(0, str(REPO / "dashboard"))
import plugin_api  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _dash_skill(home: Path, name: str, owner: str) -> None:
    d = home / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: test.\n"
        "metadata:\n"
        "  hermes:\n"
        f"    owner_profile: {owner}\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )


@pytest.fixture()
def dash_fleet(tmp_path, monkeypatch):
    home = tmp_path / "dash-home"
    _dash_skill(home, "misplaced", owner="trt")  # misplaced-global finding
    monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
    yield home


@pytest.fixture()
def dash_client(dash_fleet):
    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/skill-owner-routing")
    with TestClient(app) as tc:
        yield tc


API = "/api/plugins/skill-owner-routing"


class _FailingLedger:
    """Serves the real on-disk findings but fails every status write."""

    def __init__(self, raw_findings):
        self._raw = raw_findings

    def load_findings(self):
        return json.loads(json.dumps(self._raw))  # deep copy

    def update_status(self, finding_id, status):
        raise OSError("simulated disk failure")


class TestResolveWriteFailure:
    def test_ledger_write_failure_5xx_no_false_event(self, dash_client, dash_fleet):
        # 1. real engine populates the ledger via a normal drift read
        drift_body = dash_client.get(f"{API}/drift").json()
        finding = next(
            f for f in drift_body["findings"] if f["skill"] == "misplaced"
        )
        ledger_file = dash_fleet / "skills" / ".skill_owner_findings.json"
        raw = json.loads(ledger_file.read_text(encoding="utf-8"))["findings"]

        state_path = dash_fleet / "skill-owner-routing" / "state.json"
        history_before = (
            json.loads(state_path.read_text()).get("history", [])
            if state_path.exists()
            else []
        )

        # 2. install a ledger whose writes always fail
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                plugin_api, "_load_engine", lambda: {"ledger": _FailingLedger(raw)}
            )
            res = dash_client.post(f"{API}/drift/{finding['id']}/resolve")

        assert res.status_code == 503, res.text
        detail = res.json()["detail"]
        assert "OSError" in detail  # exception class surfaced...
        assert "simulated disk failure" not in detail  # ...internals are not
        assert "remains open" in detail

        # 3. NO false resolution: no history event, ledger still open
        history_after = (
            json.loads(state_path.read_text()).get("history", [])
            if state_path.exists()
            else []
        )
        assert history_after == history_before
        assert not any("resolved" in e.get("event", "") for e in history_after)
        statuses = {
            f["id"]: f["status"]
            for f in json.loads(ledger_file.read_text())["findings"]
        }
        assert statuses[finding["id"]] == "open"

    def test_happy_path_still_200_with_event(self, dash_client, dash_fleet):
        drift_body = dash_client.get(f"{API}/drift").json()
        finding = next(
            f for f in drift_body["findings"] if f["skill"] == "misplaced"
        )
        res = dash_client.post(f"{API}/drift/{finding['id']}/resolve")
        assert res.status_code == 200
        resolved = res.json()["finding"]
        assert resolved["status"] == "resolved"
        assert resolved["resolved_at"]

        state_path = dash_fleet / "skill-owner-routing" / "state.json"
        events = [
            e.get("event", "")
            for e in json.loads(state_path.read_text()).get("history", [])
        ]
        assert any("resolved" in e for e in events)
