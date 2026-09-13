"""Gate B — drift watchdog (SPEC-3 §Gate B, 12 rows). Rebuilt — no upstream basis.

Drives the engine's drift-scan entrypoint over a temp fleet (real FS,
real imports). Findings must serialize the SPEC-0 Interface 3 record shape:
{id, kind, severity, skill, expected_owner, actual, proposed_fix,
discovered_at, status}; kind ∈ {drifted, misplaced-global, unknown-owner,
duplicate/hoarding, unowned} (SPEC-3 B1c uses the label
"unjustified-global" — accepted provisionally; divergence recorded in
qa/matrix.md).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from qa import hook_driver as hd
from qa.contracts import make_fleet, make_skill, profile_home, root_skills_dir
from qa.engine_discovery import engine

pytestmark = pytest.mark.gate_b


def scan(fleet):
    engine()
    return hd.call_scan(hd.get_drift_scan(), fleet)


def scan_result(fleet):
    """Raw scan dict ({run_id, scanned, findings, counts}) — for B3 counts."""
    engine()
    from skill_owner_routing import drift

    saved = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(fleet)
    try:
        from skill_owner_routing import common

        common.reset_caches()
        return drift.scan()
    finally:
        if saved is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved


def kinds(findings, skill=None):
    return sorted(
        f["kind"] for f in findings if skill is None or f.get("skill") == skill
    )


def one_finding(findings, skill):
    matches = [f for f in findings if f.get("skill") == skill]
    assert len(matches) >= 1, f"no finding for {skill!r} in {[f['skill'] for f in findings]}"
    return matches[0]


@pytest.fixture()
def fleet_b(tmp_path):
    saved = os.environ.get("HERMES_HOME")
    root = make_fleet(tmp_path)
    # default home config: enabled (not strictly required for scan, but realistic)
    (root / "config.yaml").write_text("skills:\n  owner_routing:\n    enabled: true\n")
    try:
        from skill_owner_routing import common

        common.reset_caches()
        yield root
    finally:
        from skill_owner_routing import common

        common.reset_caches()
        if saved is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved


# ---------------------------------------------------------------------------
# B1 — skill in wrong profile vs owner_profile → drifted
# ---------------------------------------------------------------------------


def test_B1_wrong_profile_is_drifted(fleet_b):
    make_skill(profile_home(fleet_b, "growth"), "misplaced", owner="trt")
    findings = scan(fleet_b)
    f = one_finding(findings, "misplaced")
    hd.assert_valid_finding(f)
    assert f["kind"] == "drifted", f
    assert f["expected_owner"] == "trt"
    assert "growth" in str(f["actual"])


# ---------------------------------------------------------------------------
# B1b — global skill w/ owner_profile set → misplaced-global (hoarding signal)
# ---------------------------------------------------------------------------


def test_B1b_global_with_owner_is_misplaced_global(fleet_b):
    make_skill(fleet_b, "hoarder-global", owner="trt", scope="global")
    findings = scan(fleet_b)
    f = one_finding(findings, "hoarder-global")
    hd.assert_valid_finding(f)
    assert f["kind"] == "misplaced-global", f


# ---------------------------------------------------------------------------
# B1c — global-scope, no owner_profile, no justification → hoarding lint hit
# ---------------------------------------------------------------------------


def test_B1c_unjustified_global_flagged(fleet_b):
    make_skill(fleet_b, "bare-global", owner=None, scope="global")
    findings = scan(fleet_b)
    f = one_finding(findings, "bare-global")
    hd.assert_valid_finding(f)
    assert f["kind"] == "unowned", (
        f"B1c hoarding-lint kind must be the SPEC-0 enum 'unowned' "
        f"(SPEC-3 B1c label amended 2026-08-24): {f}"
    )


# ---------------------------------------------------------------------------
# B1d — global + valid justification → clean
# ---------------------------------------------------------------------------


def test_B1d_justified_global_is_clean(fleet_b):
    make_skill(
        fleet_b, "control-plane-global", owner=None, scope="global",
        justification="control-plane",
    )
    findings = scan(fleet_b)
    hits = [f for f in findings if f.get("skill") == "control-plane-global"]
    assert not hits, f"justified global must not raise a finding: {hits}"


@pytest.mark.parametrize("justification", ["shared-primitive", "verified-structural-dependency"])
def test_B1d_all_valid_justifications_clean(fleet_b, justification):
    make_skill(
        fleet_b, "j-global", owner=None, scope="global", justification=justification
    )
    findings = scan(fleet_b)
    hits = [f for f in findings if f.get("skill") == "j-global"]
    assert not hits, f"justification {justification!r} must be clean: {hits}"


# ---------------------------------------------------------------------------
# B2 — owner id not a registered profile → unknown-owner
# ---------------------------------------------------------------------------


def test_B2_unknown_owner_flagged(fleet_b):
    make_skill(profile_home(fleet_b, "growth"), "ghost-owned", owner="missing-profile")
    findings = scan(fleet_b)
    f = one_finding(findings, "ghost-owned")
    hd.assert_valid_finding(f)
    assert f["kind"] == "unknown-owner", f


# ---------------------------------------------------------------------------
# B2b — global copy + profile copy both exist → duplicate/hoarding
# ---------------------------------------------------------------------------


def test_B2b_global_and_profile_copies_flagged(fleet_b):
    make_skill(fleet_b, "dup-skill", owner=None, scope="global")
    make_skill(profile_home(fleet_b, "trt"), "dup-skill", owner="trt")
    findings = scan(fleet_b)
    matches = [f for f in findings if f.get("skill") == "dup-skill"]
    assert matches, "no finding for duplicate skill"
    for f in matches:
        hd.assert_valid_finding(f)
    kinds = {f["kind"] for f in matches}
    assert "duplicate/hoarding" in kinds, (
        f"global+profile duplicate must raise duplicate/hoarding; got {kinds}"
    )


# ---------------------------------------------------------------------------
# B3 — N=500 skills scan completes <10s, no partial state
# ---------------------------------------------------------------------------


def test_B3_scan_500_skills_under_10s(fleet_b):
    n = 500
    t0 = time.perf_counter()
    for i in range(n):
        owner = ("trt", "growth", None)[i % 3]
        make_skill(
            profile_home(fleet_b, "trt" if i % 2 else "growth"),
            f"bulk-{i:04d}",
            owner=owner,
        )
    fixture_build = time.perf_counter() - t0
    assert fixture_build < 60, "fixture construction itself should be fast"

    t1 = time.perf_counter()
    result = scan_result(fleet_b)
    elapsed = time.perf_counter() - t1
    assert elapsed < 10.0, f"scan of {n} skills took {elapsed:.2f}s (budget 10s)"

    # no partial state: every skill dir was cataloged exactly once (clean
    # skills legitimately produce no finding — completeness is proven via
    # the scan's own "scanned" count + per-skill findings where expected)
    assert result["scanned"] >= n, (
        f"scan examined {result['scanned']} of {n} skills (partial state)"
    )
    bulk = [f for f in result["findings"] if str(f.get("skill", "")).startswith("bulk-")]
    skills_seen = {f["skill"] for f in bulk}

    def owner_of(i):
        return ("trt", "growth", None)[i % 3]

    def placed_in(i):
        return "trt" if i % 2 else "growth"

    # drifted iff a declared owner sits in the WRONG profile; ownerless
    # (profile-scope) and correctly-placed skills are legitimately clean
    expected = {
        f"bulk-{i:04d}"
        for i in range(n)
        if owner_of(i) is not None and owner_of(i) != placed_in(i)
    }
    missing = expected - skills_seen
    assert not missing, f"scan dropped findings (partial state): {sorted(missing)[:5]}…"
    assert not (skills_seen - {f'bulk-{i:04d}' for i in range(n)}), "unexpected extra bulk findings"


# ---------------------------------------------------------------------------
# B3b — concurrent scan + create → no race, no lost findings
# ---------------------------------------------------------------------------


def test_B3b_concurrent_scan_and_create(fleet_b):
    make_skill(profile_home(fleet_b, "growth"), "seed-drift", owner="trt")

    errors: list[str] = []
    scan_counts: list[int] = []
    created: list[str] = []

    def scanner():
        try:
            for _ in range(5):
                fs = scan(fleet_b)
                scan_counts.append(len(fs))
                time.sleep(0.01)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"scan: {exc}")

    def creator():
        try:
            for i in range(10):
                make_skill(profile_home(fleet_b, "trt"), f"conc-{i:02d}", owner="trt")
                created.append(f"conc-{i:02d}")
                time.sleep(0.01)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"create: {exc}")

    t_scan = threading.Thread(target=scanner)
    t_create = threading.Thread(target=creator)
    t_scan.start()
    t_create.start()
    t_scan.join(timeout=60)
    t_create.join(timeout=60)

    assert not errors, f"concurrency errors: {errors}"
    assert not t_scan.is_alive() and not t_create.is_alive(), "threads hung"

    # final state: every created skill is present on disk and the seed drift
    # is still reported — no lost findings
    final = scan(fleet_b)
    final_names = {f["skill"] for f in final}
    assert "seed-drift" in final_names, "lost finding: seed drift vanished after concurrency"
    for name in created:
        p = profile_home(fleet_b, "trt") / "skills" / name / "SKILL.md"
        assert p.is_file(), f"created skill missing after concurrent scan: {name}"


# ---------------------------------------------------------------------------
# B4 — propose-fix path: proposes, never auto-mutates
# ---------------------------------------------------------------------------


def test_B4_proposes_never_auto_mutates(fleet_b):
    make_skill(profile_home(fleet_b, "growth"), "propose-me", owner="trt")
    before = hd.snapshot_tree_of(fleet_b)
    findings = scan(fleet_b)
    f = one_finding(findings, "propose-me")
    hd.assert_valid_finding(f)
    assert f.get("proposed_fix"), f"drift finding must carry a proposed_fix: {f}"
    after = hd.snapshot_tree_of(fleet_b)
    assert before == after, "watchdog auto-mutated fleet state (must be approval-gated)"


# ---------------------------------------------------------------------------
# B4b — audit run via REST/CLI: 202 + run_id; result lands in feed; idempotent
# ---------------------------------------------------------------------------


def test_B4b_audit_run_rest_contract(fleet_b, monkeypatch, tmp_path):
    engine()
    make_skill(profile_home(fleet_b, "growth"), "rest-drift", owner="trt")

    api_path = None
    from qa.contracts import plugin_root

    for cand in (plugin_root() / "dashboard" / "plugin_api.py",):
        if cand.is_file():
            api_path = cand
            break
    if api_path is None:
        pytest.skip(
            "BUILD-2 plugin_api.py not present — REST row will activate when it lands"
        )

    import importlib.util

    spec = importlib.util.spec_from_file_location("plugin_api_under_test", api_path)
    assert spec is not None and spec.loader is not None, f"cannot load {api_path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    router = getattr(mod, "router", None) or pytest.fail("plugin_api.py must expose 'router'")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    # Mount exactly like the web-server loader does (web_server._mount_plugin_api_routes):
    # plugin routers expose bare paths; the /api/plugins/<name> prefix is applied here.
    app.include_router(router, prefix="/api/plugins/skill-owner-routing")
    client = TestClient(app)

    monkeypatch.setenv("HERMES_HOME", str(fleet_b))

    r1 = client.post("/api/plugins/skill-owner-routing/audit/run")
    assert r1.status_code == 202, f"audit run must 202, got {r1.status_code}: {r1.text[:200]}"
    body1 = r1.json()
    assert body1.get("run_id"), f"202 must carry run_id: {body1}"

    # idempotent re-run: second identical request also 202s (no duplicate side effects)
    r2 = client.post("/api/plugins/skill-owner-routing/audit/run")
    assert r2.status_code == 202, f"idempotent re-run must 202: {r2.status_code}"

    # result lands in the drift feed
    feed = client.get("/api/plugins/skill-owner-routing/drift")
    assert feed.status_code == 200, feed.text[:200]
    payload = feed.json()
    items = payload.get("findings") if isinstance(payload, dict) else payload
    assert isinstance(items, list), f"feed shape: {type(payload)}"
    names = {f.get("skill") for f in items}
    assert "rest-drift" in names, f"audit result missing from feed: sorted names sample {sorted(names)[:10]}"
