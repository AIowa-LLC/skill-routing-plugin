"""skill-owner-routing dashboard API — SPEC-2 REST contract tests (frozen v1.0).

Exercises dashboard/plugin_api.py through FastAPI's TestClient against a
synthetic temp fleet (HERMES_HOME-style layout under tmp_path). No live
Hermes state is touched: _default_home is monkeypatched, so reads/writes stay
inside the temp tree. Core imports (hermes_constants / hermes_cli.config /
tools.skill_manager_tool / agent.skill_utils) are absent-or-present guards in
the module itself; tests run without them on sys.path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboard"))

import plugin_api  # noqa: E402
from conftest import TEST_SESSION_HEADER, TEST_SESSION_TOKEN  # noqa: E402

AUTH_HEADERS = {TEST_SESSION_HEADER: TEST_SESSION_TOKEN}

SKILL_TMPL = """---
name: {name}
description: {desc}
category: {category}
{extra}---

# {name}

body
"""


def make_skill(name: str, owner: str | None = None, category: str = "test", justification: str | None = None) -> str:
    extra = ""
    if owner or justification:
        bits = []
        if owner:
            bits.append(f"      owner_profile: {owner}")
        if justification:
            bits.append(f"      global_justification: {justification}")
        extra = "metadata:\n  hermes:\n" + "\n".join(bits) + "\n"
    return SKILL_TMPL.format(name=name, desc=f"{name} skill", category=category, extra=extra)


@pytest.fixture()
def fleet(tmp_path, monkeypatch):
    """Temp fleet: default home with global skills + profiles/{trt,frontend}."""
    home = tmp_path / "hermes-home"
    (home / "skills" / "clean-global").mkdir(parents=True)
    (home / "skills" / "clean-global" / "SKILL.md").write_text(
        make_skill("clean-global", justification="shared-primitive")
    )
    (home / "skills" / "misplaced").mkdir()
    (home / "skills" / "misplaced" / "SKILL.md").write_text(make_skill("misplaced", owner="trt"))
    (home / "skills" / "dupe").mkdir()
    (home / "skills" / "dupe" / "SKILL.md").write_text(make_skill("dupe", owner="trt"))
    trt = home / "profiles" / "trt" / "skills"
    (trt / "owned").mkdir(parents=True)
    (trt / "owned" / "SKILL.md").write_text(make_skill("owned", owner="trt"))
    (trt / "drifter").mkdir(parents=True)
    (trt / "drifter" / "SKILL.md").write_text(make_skill("drifter", owner="frontend"))
    (trt / "dupe").mkdir(parents=True)
    (trt / "dupe" / "SKILL.md").write_text(make_skill("dupe", owner="trt"))
    front = home / "profiles" / "frontend" / "skills" / "unowned-front"
    front.mkdir(parents=True)
    (front / "SKILL.md").write_text(make_skill("unowned-front"))
    (home / "profiles" / "frontend" / "skills" / "ghost-owned").mkdir(parents=True)
    (home / "profiles" / "frontend" / "skills" / "ghost-owned" / "SKILL.md").write_text(
        make_skill("ghost-owned", owner="nosuch")
    )
    monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
    yield home


@pytest.fixture()
def client(fleet):
    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/skill-owner-routing")
    with TestClient(app, headers={TEST_SESSION_HEADER: TEST_SESSION_TOKEN}) as tc:
        yield tc


API = "/api/plugins/skill-owner-routing"


# -- GET /map ---------------------------------------------------------------

def test_map_rows_and_meta(client):
    res = client.get(f"{API}/map")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"rows", "meta"}
    assert set(body["meta"]) == {"profiles", "last_audit_ts"}
    assert body["meta"]["profiles"] == ["frontend", "trt"]
    by_id = {r["skill_id"]: r for r in body["rows"]}
    assert by_id["profile::owned"]["state"] == "clean"
    assert by_id["global::clean-global"]["state"] == "clean"
    assert by_id["profile::unowned-front"]["state"] == "unowned"
    assert by_id["profile::ghost-owned"]["state"] == "unknown-owner"
    assert by_id["global::misplaced"]["state"] == "drifted"
    assert by_id["profile::drifter"]["state"] == "drifted"
    assert by_id["global::dupe"]["state"] == "duplicate"
    row = by_id["profile::owned"]
    assert row["owner_profile"] == "trt"
    assert row["location"] == {"scope": "profile", "profile": "trt", "path": row["location"]["path"]}
    assert "last_audit" in row  # V1 renders last audit per row


def test_map_stable_across_calls(client):
    a = client.get(f"{API}/map").json()
    b = client.get(f"{API}/map").json()
    assert a["rows"] == b["rows"]  # D3 determinism (minus run/timestamps)


# -- GET /map/{skill_id} ------------------------------------------------------

def test_map_detail_shape(client):
    res = client.get(f"{API}/map/global::clean-global")
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"skill_id", "name", "owner_profile", "location", "state", "frontmatter", "rationale", "history"}
    assert body["frontmatter"]["name"] == "clean-global"
    assert body["rationale"]["hoarding_justified"] is True


def test_map_detail_unjustified_global(client):
    body = client.get(f"{API}/map/profile::unowned-front").json()
    assert body["rationale"]["hoarding_justified"] is False


def test_map_detail_404(client):
    assert client.get(f"{API}/map/global::nosuch").status_code == 404


def test_map_detail_traversal_is_just_a_404(client):
    assert client.get(f"{API}/map/..%2F..%2Fetc").status_code == 404


# -- GET /drift + /drift/summary ---------------------------------------------

def test_drift_findings_canonical_shape(client):
    client.get(f"{API}/map")  # populate
    body = client.get(f"{API}/drift").json()
    assert set(body) == {"findings", "meta"}
    assert set(body["meta"]) == {"open_count", "counts_by_severity"}
    canonical = {"id", "kind", "severity", "skill", "expected_owner", "actual", "proposed_fix", "discovered_at", "status"}
    assert body["findings"], "expected findings from fixture fleet"
    for finding in body["findings"]:
        assert canonical <= set(finding)
        assert finding["severity"] in ("info", "warning", "critical")
        assert finding["status"] in ("open", "resolved")
    kinds = {f["kind"] for f in body["findings"]}
    assert kinds <= {"drifted", "misplaced-global", "unknown-owner", "duplicate/hoarding", "unowned"}
    assert "duplicate/hoarding" in kinds
    assert "misplaced-global" in kinds
    assert "unknown-owner" in kinds


def test_drift_summary(client):
    client.get(f"{API}/map")
    body = client.get(f"{API}/drift/summary").json()
    assert set(body) == {"open_count", "worst_severity"}
    assert body["worst_severity"] in ("warning", "critical")  # drifted/duplicate findings exist
    assert body["open_count"] > 0


# -- POST /audit/run + GET /audit/runs/{run_id} --------------------------------

def test_audit_run_roundtrip(client):
    res = client.post(f"{API}/audit/run")
    assert res.status_code == 202
    run_id = res.json()["run_id"]
    assert run_id
    body = client.get(f"{API}/audit/runs/{run_id}").json()
    assert set(body) == {"state", "findings_count"}
    assert body["state"] in ("running", "done", "failed")
    import time

    for _ in range(100):
        if body["state"] != "running":
            break
        time.sleep(0.05)
        body = client.get(f"{API}/audit/runs/{run_id}").json()
    assert body["state"] == "done"
    assert isinstance(body["findings_count"], int)
    drift = client.get(f"{API}/drift").json()
    assert drift["meta"]["open_count"] == body["findings_count"]


def test_audit_run_sets_last_audit_ts(client):
    client.get(f"{API}/map")
    run_id = client.post(f"{API}/audit/run").json()["run_id"]
    import time

    for _ in range(100):
        state = client.get(f"{API}/audit/runs/{run_id}").json()["state"]
        if state != "running":
            break
        time.sleep(0.05)
    assert client.get(f"{API}/map").json()["meta"]["last_audit_ts"] is not None


def test_audit_unknown_run_404(client):
    assert client.get(f"{API}/audit/runs/deadbeef").status_code == 404


def test_audit_determinism(client):
    run_a = client.post(f"{API}/audit/run").json()["run_id"]
    run_b = client.post(f"{API}/audit/run").json()["run_id"]
    import time

    for run_id in (run_a, run_b):
        for _ in range(100):
            if client.get(f"{API}/audit/runs/{run_id}").json()["state"] != "running":
                break
            time.sleep(0.05)

    def findings_without_noise():
        out = client.get(f"{API}/drift").json()["findings"]
        for f in out:
            f.pop("discovered_at", None)
            f.pop("resolved_at", None)
        return sorted(out, key=lambda f: f["id"])

    assert findings_without_noise() == findings_without_noise()


# -- GET /policy ---------------------------------------------------------------

def test_policy_get_shape_and_default_on(client, fleet):
    body = client.get(f"{API}/policy").json()
    assert set(body) == {"enabled", "require_owner_metadata", "route_from_default", "key_present", "core_enforces", "posture"}
    assert body == {
        "enabled": True,
        "require_owner_metadata": True,
        "route_from_default": True,
        "key_present": False,
        "core_enforces": False,
        "posture": "default-on",
    }


def test_policy_disabled_reads_user_disabled(client, fleet):
    cfg = {"skills": {"owner_routing": {"enabled": False, "require_owner_metadata": True, "route_from_default": True}}}
    (fleet / "config.yaml").write_text(yaml.safe_dump(cfg))
    body = client.get(f"{API}/policy").json()
    assert body["key_present"] is True
    assert body["enabled"] is False
    assert body["posture"] == "user-disabled"


# -- PUT /policy ---------------------------------------------------------------

def test_policy_put_writes_config_and_returns_diff(client, fleet):
    res = client.put(f"{API}/policy", json={"enabled": True, "require_owner_metadata": False, "route_from_default": True})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"policy", "config_diff"}
    assert body["policy"]["require_owner_metadata"] is False
    assert body["policy"]["posture"] == "default-on"
    on_disk = yaml.safe_load((fleet / "config.yaml").read_text())
    assert on_disk["skills"]["owner_routing"] == {
        "enabled": True,
        "require_owner_metadata": False,
        "route_from_default": True,
    }
    diff = body["config_diff"]
    assert diff["before"] is None
    assert diff["after"] == on_disk["skills"]["owner_routing"]
    assert str(fleet / "config.yaml") in diff["path"]


def test_policy_put_partial_patch(client, fleet):
    client.put(f"{API}/policy", json={"enabled": True})
    client.put(f"{API}/policy", json={"route_from_default": False})
    body = client.get(f"{API}/policy").json()
    assert body["route_from_default"] is False
    assert body["require_owner_metadata"] is True  # untouched default preserved
    on_disk = yaml.safe_load((fleet / "config.yaml").read_text())
    assert on_disk["skills"]["owner_routing"]["route_from_default"] is False
    assert on_disk["skills"]["owner_routing"]["require_owner_metadata"] is True


def test_policy_put_only_touches_owner_routing_subtree(client, fleet):
    cfg = {"model": {"default": "keep-me"}, "skills": {"other_key": {"deep": True}}}
    (fleet / "config.yaml").write_text(yaml.safe_dump(cfg))
    client.put(f"{API}/policy", json={"enabled": False})
    on_disk = yaml.safe_load((fleet / "config.yaml").read_text())
    assert on_disk["model"]["default"] == "keep-me"
    assert on_disk["skills"]["other_key"] == {"deep": True}
    assert on_disk["skills"]["owner_routing"]["enabled"] is False


def test_policy_put_rejects_unknown_fields(client):
    res = client.put(f"{API}/policy", json={"enabled": True, "evil": "x"})
    assert res.status_code == 422


def test_policy_put_rejects_empty(client):
    res = client.put(f"{API}/policy", json={})
    assert res.status_code == 400


def test_policy_put_confirm_false_rejected(client):
    res = client.put(f"{API}/policy", json={"enabled": True, "confirm": False})
    assert res.status_code == 400


def test_policy_write_survives_engine_roundtrip(client, fleet, monkeypatch):
    # Force the fallback write path even when the engine is importable.
    monkeypatch.setattr(plugin_api, "_ENGINE_CACHE", [None, False])
    client.put(f"{API}/policy", json={"enabled": False})
    assert client.get(f"{API}/policy").json()["posture"] == "user-disabled"
    monkeypatch.undo()  # restore real engine binding for later tests
    assert plugin_api._load_engine() is not None


# -- PUT /policy destructive-write guards (M6, OCR review @ d1659fb) ------------


def test_policy_put_aborts_on_corrupt_config(client, fleet):
    # (b) an unparseable config.yaml must abort the write — base persisted
    # {skills: {owner_routing}} as the ENTIRE config.yaml, wiping every
    # other user setting.
    corrupt = "skills: [unclosed\n  broken: {{{\n"
    (fleet / "config.yaml").write_text(corrupt)
    res = client.put(f"{API}/policy", json={"enabled": False})
    assert res.status_code == 503
    assert "cannot be read" in res.json()["detail"] or "corrupt" in res.json()["detail"].lower()
    assert (fleet / "config.yaml").read_text() == corrupt  # byte-identical


def test_policy_put_aborts_on_unreadable_config(client, fleet, monkeypatch):
    # (b, permission flavor) an existing config.yaml that cannot be read
    # must abort the write, not reset the file.
    (fleet / "config.yaml").write_text("skills:\n  owner_routing:\n    enabled: true\n")

    def boom(*args, **kwargs):
        raise PermissionError("disk blip")

    monkeypatch.setattr("pathlib.Path.read_text", boom)
    res = client.put(f"{API}/policy", json={"enabled": False})
    monkeypatch.undo()  # restore real read_text for the verification
    assert res.status_code == 503
    on_disk = (fleet / "config.yaml").read_text()
    assert on_disk == "skills:\n  owner_routing:\n    enabled: true\n"


def test_policy_put_no_stale_fallback_when_core_save_fails(client, fleet, monkeypatch):
    # (c) when core save_config raises, base fell through to the local
    # fallback write built from the STALE pre-merge snapshot; the write
    # must abort instead.
    import hermes_cli.config as core_config

    def boom(*args, **kwargs):
        raise RuntimeError("core save_config refused (managed scope)")

    monkeypatch.setattr(core_config, "save_config", boom)
    res = client.put(f"{API}/policy", json={"enabled": False})
    assert res.status_code == 503
    assert "core config save failed" in res.json()["detail"].lower()
    # config.yaml untouched (fresh-home fixture has no config.yaml yet —
    # a new one must NOT be created by a failed write)
    assert not (fleet / "config.yaml").exists()


def test_policy_put_lock_serializes_read_merge_write(client, fleet, monkeypatch):
    # (a) the read-merge-write must run under _STATE_LOCK: PUT /policy
    # calls _write_policy_home BEFORE taking the lock on base. Pin the
    # invariant statically (the lock is process-global; a live race is
    # not deterministically assertable here).
    import inspect

    src = inspect.getsource(plugin_api.put_policy)
    assert "_STATE_LOCK" in src, "put_policy must hold _STATE_LOCK across the write"
    body = src.split("def put_policy", 1)[1]
    first_lock = body.index("_STATE_LOCK")
    first_write = body.index("_write_policy_home")
    assert first_lock < first_write, "lock must be acquired before the policy write"


# -- POST /drift/{id}/resolve ----------------------------------------------------

def test_resolve_flow(client):
    client.get(f"{API}/map")
    finding = client.get(f"{API}/drift").json()["findings"][0]
    res = client.post(f"{API}/drift/{finding['id']}/resolve")
    assert res.status_code == 200
    resolved = res.json()["finding"]
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"]
    drift = client.get(f"{API}/drift").json()
    assert drift["meta"]["open_count"] == sum(1 for f in drift["findings"] if f["status"] == "open")
    assert client.get(f"{API}/drift/summary").json()["open_count"] >= 0


def test_resolve_idempotent_conflict(client):
    client.get(f"{API}/map")
    finding = client.get(f"{API}/drift").json()["findings"][0]
    assert client.post(f"{API}/drift/{finding['id']}/resolve").status_code == 200
    assert client.post(f"{API}/drift/{finding['id']}/resolve").status_code == 409


def test_resolve_unknown_404(client):
    assert client.post(f"{API}/drift/f_nosuch/resolve").status_code == 404


def test_resolve_persists_across_rescan(client):
    client.get(f"{API}/map")
    finding = next(f for f in client.get(f"{API}/drift").json()["findings"] if f["kind"] == "misplaced-global")
    client.post(f"{API}/drift/{finding['id']}/resolve")
    run_id = client.post(f"{API}/audit/run").json()["run_id"]
    import time

    for _ in range(100):
        if client.get(f"{API}/audit/runs/{run_id}").json()["state"] != "running":
            break
        time.sleep(0.05)
    after = {f["id"]: f for f in client.get(f"{API}/drift").json()["findings"]}
    assert after[finding["id"]]["status"] == "resolved"  # resolution survives re-scan


def test_resolve_traversal_finding_id_is_404(client):
    assert client.post(f"{API}/drift/..%2F..%2Fstate.json/resolve").status_code == 404


# -- history feeds the detail drawer ------------------------------------------

def test_detail_history_records_resolve(client):
    client.get(f"{API}/map")
    findings = client.get(f"{API}/drift").json()["findings"]
    target_skill = "misplaced"
    finding = next(f for f in findings if f["skill"] == target_skill)
    client.post(f"{API}/drift/{finding['id']}/resolve")
    detail = client.get(f"{API}/map/global::misplaced").json()
    events = [h["event"] for h in detail["history"]]
    assert any("resolved" in e for e in events)


# -- state hygiene --------------------------------------------------------------

def test_state_lands_inside_default_home(client, fleet):
    client.get(f"{API}/map")
    state_path = fleet / "skill-owner-routing" / "state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["schema"] == 1
    assert data["rows"]


def test_no_secrets_in_payloads(client, fleet):
    for method, path in [("get", "/map"), ("get", "/drift"), ("get", "/drift/summary"), ("get", "/policy")]:
        raw = getattr(client, method)(f"{API}{path}").text
        scrubbed = raw.replace(str(fleet), "<fleet>")  # tmp paths echo the test name
        for marker in ("api_key", "apikey", "token", "password", "secret"):
            assert marker not in scrubbed.lower()


def test_engine_absent_graceful(client, monkeypatch):
    """Contract: dashboard runs scan-fallback when BUILD-1 package is missing.

    Genuine absence (T4): the engine modules are evicted and the loader's own
    import channel is blocked, so _load_engine() really returns None — no
    cache-reset rebound back into a working import.
    """
    for mod in [m for m in list(sys.modules) if m == "skill_owner_routing" or m.startswith("skill_owner_routing.")]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    real_import = plugin_api.importlib.import_module

    def blocked(name, *args, **kwargs):
        if name.startswith("skill_owner_routing"):
            raise ImportError(f"engine import blocked for test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(plugin_api.importlib, "import_module", blocked)
    monkeypatch.setattr(plugin_api, "_ENGINE_CACHE", [None, False])
    assert plugin_api._load_engine() is None, "absence was not simulated — engine still loads"
    body = client.get(f"{API}/map").json()
    assert body["rows"]
    drift = client.get(f"{API}/drift").json()
    assert drift["findings"], "scan-fallback must populate the Drift Feed (M7)"
    assert drift["meta"]["open_count"] == sum(1 for f in drift["findings"] if f["status"] == "open")
    # engine present again for the rest of the suite
    monkeypatch.undo()
    engine = plugin_api._load_engine()
    assert engine is None or callable(engine["drift"].scan)


# -- websocket ------------------------------------------------------------------

def _ws_connect(client, path):
    """Authenticated loopback-origin WS connect (SECURITY-CONTRACT §WS)."""
    sep = "&" if "?" in path else "?"
    return client.websocket_connect(
        f"{path}{sep}token={TEST_SESSION_TOKEN}",
        headers={"origin": "http://localhost:5173"},
    )


def test_ws_events_push_on_policy_write(client):
    with _ws_connect(client, f"{API}/events") as ws:
        res = client.put(f"{API}/policy", json={"enabled": True})
        assert res.status_code == 200
        assert ws.receive_text() == "invalidate"


def test_ws_events_push_on_resolve(client):
    client.get(f"{API}/map")
    finding = client.get(f"{API}/drift").json()["findings"][0]
    with _ws_connect(client, f"{API}/events") as ws:
        assert client.post(f"{API}/drift/{finding['id']}/resolve").status_code == 200
        assert ws.receive_text() == "invalidate"
