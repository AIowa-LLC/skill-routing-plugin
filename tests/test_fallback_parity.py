"""M7 scan-fallback parity — the dashboard works WITHOUT the engine.

The public kit ships the dashboard alone (engine optional). SPEC-2 promises
the built-in scan-fallback serves the same canonical output shape. Master
report M7: fallback mode served a permanently empty Drift Feed and a wrong
summary — nothing populated state["findings"]; the fallback findings computed
in _rows_from were discarded.

These tests run the dashboard with the engine GENUINELY absent (modules
evicted + the loader's import channel blocked) and pin:
  - /drift and /drift/summary populated + parity with each other
  - canonical SPEC-0 Interface 3 finding shape
  - audit runs refresh the fallback feed (findings_count == open_count)
  - resolve persists across rescans (upsert keeps resolved status)
  - fixed-on-disk drift drops out of the feed
  - V1/V2/V3/V4/V5 surfaces all render sane data engine-absent
  - pre-fix state.json (rows, no findings key) is backfilled
  - fallback finding ids are path-digested (M5 twin parity)
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboard"))

import plugin_api  # noqa: E402
from conftest import TEST_SESSION_HEADER, TEST_SESSION_TOKEN  # noqa: E402

# Security contract (D1) made session-token auth mandatory on every REST
# template — fail-closed when a token is configured. These fallback tests
# predate that lane; their clients authenticate like test_dashboard_api.
AUTH_HEADERS = {TEST_SESSION_HEADER: TEST_SESSION_TOKEN}

API = "/api/plugins/skill-owner-routing"

SKILL_TMPL = """---
name: {name}
description: {desc}
category: {category}
{extra}---

# {name}

body
"""


def make_skill(
    name: str,
    owner: str | None = None,
    category: str = "test",
    justification: str | None = None,
) -> str:
    extra = ""
    if owner or justification:
        bits = []
        if owner:
            bits.append(f"      owner_profile: {owner}")
        if justification:
            bits.append(f"      global_justification: {justification}")
        extra = "metadata:\n  hermes:\n" + "\n".join(bits) + "\n"
    return SKILL_TMPL.format(name=name, desc=f"{name} skill", category=category, extra=extra)


def write_skill(skills_dir: Path, name: str, category: str | None = None, **fm) -> None:
    d = skills_dir / (category or "") / name if category else skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(make_skill(name, **fm))


@pytest.fixture()
def drift_fleet(tmp_path, monkeypatch):
    """Fleet with one drift instance of each fallback finding kind."""
    home = tmp_path / "hermes-home"
    write_skill(home / "skills", "misplaced", owner="trt")                    # misplaced-global
    write_skill(home / "skills", "clean-global", justification="shared-primitive")  # clean
    write_skill(home / "skills", "dupe", owner="trt")                         # duplicate/hoarding
    write_skill(home / "profiles" / "trt" / "skills", "dupe", owner="trt")
    write_skill(home / "profiles" / "trt" / "skills", "owned", owner="trt")   # clean
    write_skill(home / "profiles" / "trt" / "skills", "drifter", owner="frontend")  # drifted
    write_skill(home / "profiles" / "frontend" / "skills", "unowned-front")   # unowned
    write_skill(home / "profiles" / "frontend" / "skills", "ghost", owner="nosuch")  # unknown-owner
    monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
    yield home


@pytest.fixture()
def no_engine(monkeypatch):
    """Genuine engine absence: evict modules + block the loader's imports.

    Unlike patching _ENGINE_CACHE back to [None, False] (which merely resets
    the probe — the real engine imports fine and rebounds), this makes
    _load_engine() return None for real, exactly like a dashboard-only kit.
    """
    for mod in [m for m in list(sys.modules) if m == "skill_owner_routing" or m.startswith("skill_owner_routing.")]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    real_import = importlib.import_module

    def blocked(name, *args, **kwargs):
        if name.startswith("skill_owner_routing"):
            raise ImportError(f"engine import blocked for test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(plugin_api.importlib, "import_module", blocked)
    monkeypatch.setattr(plugin_api, "_ENGINE_CACHE", [None, False])
    assert plugin_api._load_engine() is None, "engine absence was not simulated"
    yield
    # monkeypatch teardown restores sys.modules entries + cache


@pytest.fixture()
def fallback_client(drift_fleet, no_engine):
    app = FastAPI()
    app.include_router(plugin_api.router, prefix=API)
    with TestClient(app, headers=AUTH_HEADERS) as tc:
        yield tc


def _wait_run(client, run_id):
    for _ in range(100):
        body = client.get(f"{API}/audit/runs/{run_id}").json()
        if body["state"] != "running":
            return body
        time.sleep(0.05)
    return client.get(f"{API}/audit/runs/{run_id}").json()


# -- V2 + V4: the Drift Feed + summary chip (M7 core) -------------------------


class TestFallbackFeedPopulated:
    def test_drift_populated_without_map_first(self, fallback_client):
        """Cold start: /drift as the FIRST call must serve findings (the feed
        must not depend on a prior /map having seeded state)."""
        body = fallback_client.get(f"{API}/drift").json()
        assert body["findings"], "fallback Drift Feed is empty (M7)"
        kinds = {f["kind"] for f in body["findings"]}
        assert kinds == {
            "misplaced-global",
            "duplicate/hoarding",
            "drifted",
            "unowned",
            "unknown-owner",
        }

    def test_summary_parity_with_drift(self, fallback_client):
        drift = fallback_client.get(f"{API}/drift").json()
        summary = fallback_client.get(f"{API}/drift/summary").json()
        open_findings = [f for f in drift["findings"] if f["status"] == "open"]
        assert summary["open_count"] == drift["meta"]["open_count"] == len(open_findings)
        assert summary["open_count"] > 0
        assert summary["worst_severity"] == "critical"  # drifter is critical

    def test_counts_by_severity_match_findings(self, fallback_client):
        drift = fallback_client.get(f"{API}/drift").json()
        counts = drift["meta"]["counts_by_severity"]
        for sev in ("info", "warning", "critical"):
            assert counts[sev] == sum(
                1 for f in drift["findings"] if f["status"] == "open" and f["severity"] == sev
            )
        assert sum(counts.values()) == drift["meta"]["open_count"]

    def test_findings_canonical_shape(self, fallback_client):
        body = fallback_client.get(f"{API}/drift").json()
        canonical = {"id", "kind", "severity", "skill", "expected_owner", "actual",
                     "proposed_fix", "discovered_at", "status"}
        for finding in body["findings"]:
            assert canonical <= set(finding), finding
            assert finding["severity"] in ("info", "warning", "critical")
            assert finding["status"] == "open"
            assert isinstance(finding["discovered_at"], int) and finding["discovered_at"] > 0
            assert finding["id"] and finding["proposed_fix"]

    def test_seeded_feed_persists_in_state(self, fallback_client, drift_fleet):
        fallback_client.get(f"{API}/drift")
        state = json.loads(
            (drift_fleet / "skill-owner-routing" / "state.json").read_text(encoding="utf-8")
        )
        assert state.get("findings"), "seeded fallback findings must persist in state.json"

    def test_clean_fleet_serves_empty_not_error(self, tmp_path, monkeypatch, no_engine):
        home = tmp_path / "hermes-home"
        write_skill(home / "skills", "only-clean", justification="control-plane")
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix=API)
        with TestClient(app, headers=AUTH_HEADERS) as client:
            drift = client.get(f"{API}/drift").json()
            summary = client.get(f"{API}/drift/summary").json()
        assert drift["findings"] == []
        assert drift["meta"]["open_count"] == 0
        assert summary == {"open_count": 0, "worst_severity": None}


# -- V3: audit runs refresh the fallback feed ---------------------------------


class TestFallbackAudit:
    def test_audit_refreshes_feed_and_counts_match(self, fallback_client):
        run_id = fallback_client.post(f"{API}/audit/run").json()["run_id"]
        record = _wait_run(fallback_client, run_id)
        assert record["state"] == "done"
        drift = fallback_client.get(f"{API}/drift").json()
        assert drift["findings"]
        assert record["findings_count"] == drift["meta"]["open_count"], (
            "audit findings_count must equal the feed it just refreshed (M7)"
        )
        summary = fallback_client.get(f"{API}/drift/summary").json()
        assert summary["open_count"] == record["findings_count"]

    def test_resolve_persists_across_fallback_rescan(self, fallback_client):
        fallback_client.get(f"{API}/drift")
        finding = next(
            f for f in fallback_client.get(f"{API}/drift").json()["findings"]
            if f["kind"] == "misplaced-global"
        )
        res = fallback_client.post(f"{API}/drift/{finding['id']}/resolve")
        assert res.status_code == 200
        assert res.json()["finding"]["status"] == "resolved"
        run_id = fallback_client.post(f"{API}/audit/run").json()["run_id"]
        _wait_run(fallback_client, run_id)
        after = {f["id"]: f for f in fallback_client.get(f"{API}/drift").json()["findings"]}
        assert after[finding["id"]]["status"] == "resolved", (
            "fallback resolution must survive a rescan (upsert parity)"
        )

    def test_fixed_drift_drops_from_feed(self, fallback_client, drift_fleet):
        fallback_client.get(f"{API}/drift")
        before = [f for f in fallback_client.get(f"{API}/drift").json()["findings"]
                  if f["skill"] == "misplaced"]
        assert before
        # fix the drift on disk: strip the owner metadata + justify global
        md = drift_fleet / "skills" / "misplaced" / "SKILL.md"
        md.write_text(make_skill("misplaced", justification="shared-primitive"))
        run_id = fallback_client.post(f"{API}/audit/run").json()["run_id"]
        _wait_run(fallback_client, run_id)
        after = [f for f in fallback_client.get(f"{API}/drift").json()["findings"]
                 if f["skill"] == "misplaced"]
        assert not after, "fixed drift must drop out of the fallback feed"


# -- V1 + drawer: /map and detail render engine-absent ------------------------


class TestFallbackMapAndDetail:
    def test_map_rows_states_engine_absent(self, fallback_client):
        body = fallback_client.get(f"{API}/map").json()
        by_id = {r["skill_id"]: r for r in body["rows"]}
        assert by_id["global::misplaced"]["state"] == "drifted"
        assert by_id["global::clean-global"]["state"] == "clean"
        assert by_id["global::dupe"]["state"] == "duplicate"
        assert by_id["profile::owned"]["state"] == "clean"
        assert by_id["profile::drifter"]["state"] == "drifted"
        assert by_id["profile::unowned-front"]["state"] == "unowned"
        assert by_id["profile::ghost"]["state"] == "unknown-owner"
        assert body["meta"]["profiles"] == ["frontend", "trt"]

    def test_map_detail_engine_absent(self, fallback_client):
        body = fallback_client.get(f"{API}/map/global::misplaced").json()
        assert body["state"] == "drifted"
        assert body["frontmatter"]["name"] == "misplaced"
        assert body["rationale"]

    def test_policy_reads_engine_absent(self, fallback_client):
        body = fallback_client.get(f"{API}/policy").json()
        assert body["enabled"] is True  # key absent = ENABLED (plugin posture)
        assert body["posture"] == "default-on"
        assert body["key_present"] is False


# -- migration: pre-fix state.json backfill ------------------------------------


class TestPrefixStateBackfill:
    def test_rows_without_findings_key_get_backfilled(self, drift_fleet, monkeypatch, no_engine):
        # simulate a pre-M7-fix state.json: rows present, findings never written
        stale = {
            "schema": 1,
            "last_audit_ts": None,
            "last_scan_ts": None,
            "runs": {},
            "profiles": ["frontend", "trt"],
            "history": [],
            "rows": [{"skill_id": "global::misplaced", "name": "misplaced",
                      "category": "test", "owner_profile": "trt",
                      "location": {"scope": "global", "profile": None,
                                   "path": str(drift_fleet / "skills" / "misplaced")},
                      "state": "drifted"}],
        }
        state_dir = drift_fleet / "skill-owner-routing"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "state.json").write_text(json.dumps(stale), encoding="utf-8")
        monkeypatch.setattr(plugin_api, "_default_home", lambda: drift_fleet)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix=API)
        with TestClient(app, headers=AUTH_HEADERS) as client:
            drift = client.get(f"{API}/drift").json()
        assert drift["findings"], "pre-fix state must be backfilled with findings (M7)"
        assert drift["meta"]["open_count"] > 0


# -- M5 parity: fallback ids digest the resolved path -------------------------


class TestFallbackIdDigest:
    def test_twin_findings_same_kind_distinct_ids(self, tmp_path, monkeypatch, no_engine):
        """Two physical copies of one skill name, both with unknown owners —
        same kind, same skill, same scope: ids must NOT collide (the digest
        includes each copy's resolved path)."""
        home = tmp_path / "hermes-home"
        write_skill(home / "profiles" / "trt" / "skills", "twin", owner="nosuch")
        write_skill(home / "profiles" / "frontend" / "skills", "twin", owner="nosuch")
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
        scan = plugin_api._scan_fleet(home)
        twins = [f for f in scan["findings"] if f["skill"] == "twin" and f["kind"] == "unknown-owner"]
        assert len(twins) == 2, f"expected two twin findings, got {twins}"
        assert len({f["id"] for f in twins}) == 2, f"twin finding ids collided: {twins}"

    def test_fallback_ids_deterministic_across_scans(self, tmp_path, monkeypatch, no_engine):
        home = tmp_path / "hermes-home"
        write_skill(home / "skills", "misplaced", owner="trt")
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
        a = plugin_api._scan_fleet(home)["findings"]
        b = plugin_api._scan_fleet(home)["findings"]
        assert {f["id"] for f in a} == {f["id"] for f in b}
