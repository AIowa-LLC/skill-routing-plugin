"""Gate A — enforcement semantics (SPEC-3 §Gate A, 19 rows).

Ported from the PR #87101 test matrix (local commit e12d79edd1,
tests/tools/test_skill_manager_tool.py ::TestSkillOwnerRouting, 12 tests)
and extended onto the PLUGIN seam (pre_tool_call hook + skill_owner_create
tool) per SPEC-1.

Ported-matrix mapping (12 upstream tests → Gate A rows):
  policy read from default home ........ A7
  create requires owner metadata ........ A4
  default routes create to owner ........ A1 + A9b
  named cannot write sideways ........... A3
  owner==active creates locally ......... A2
  default-owned stays on default ........ A8e
  unknown owner rejected ................ A5
  invalid owner rejected pre-lookup ..... A6
  default may refuse routing ............ A8c   (NOTE: plugin posture differs
                                                 from upstream — see matrix)
  disabled preserves behavior ........... A8
  owner metadata optional ............... A8d
  real routed create (FS+usage) ......... A9b

Every test drives the REAL engine (skipped loudly until BUILD-1 lands) and
uses a temp HERMES_HOME with real imports — no mocks except the A7b core
-detection simulation, which injects the exact symbol SPEC-1 feature-detects.
"""

from __future__ import annotations

import builtins
import json
import logging
import os
import statistics
import time
from pathlib import Path

import pytest

from qa import hook_driver as hd
from qa.contracts import (
    MSG_HANDOFF,
    MSG_NOT_REGISTERED,
    MSG_REQUIRE_METADATA_SHORT,
    MSG_RESOLVE_OWNER,
    MSG_SIDEWAYS,
    OWNER_ROUTED_SKILL_CONTENT,
    VALID_SKILL_CONTENT,
    make_skill,
    profile_home,
    routed_content,
    snapshot_tree,
    switch_profile,
    write_default_config,
)
from qa.engine_discovery import engine

pytestmark = pytest.mark.gate_a

NON_SKILL_TOOL = "terminal"


def decide(cb, action, name="routed-skill", content=None, **extra):
    args = {"action": action, "name": name}
    if content is not None:
        args["content"] = content
    args.update(extra)
    return hd.call_hook(cb, "skill_manage", args)


@pytest.fixture()
def gate(fleet):
    engine()
    return hd.get_pre_tool_call_hook()


@pytest.fixture()
def enabled_config(fleet):
    return write_default_config(
        fleet, enabled=True, require_owner_metadata=True, route_from_default=True
    )


def core_skill_manage(action, name, content, **extra):
    import tools.skill_manager_tool as smt

    return json.loads(smt.skill_manage(action=action, name=name, content=content, **extra))


# ---------------------------------------------------------------------------
# A1 — default + create, owner=trt → deny-redirect; routed create lands in trt
# ---------------------------------------------------------------------------


def test_A1_default_create_owner_trt_redirects_to_routed_create(gate, fleet, enabled_config):
    switch_profile("default", fleet)
    d = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
    assert hd.is_block(d), f"expected deny-redirect, got {d!r}"
    msg = hd.block_message(d)
    assert MSG_HANDOFF in msg, f"handoff contract missing: {msg!r}"

    # the redirect target performs the routed transaction (A9b proves the
    # full round trip; here we prove the create lands under the owner)
    tool = hd.get_routed_create()
    res = hd.call_tool(tool, {"name": "routed-skill", "content": OWNER_ROUTED_SKILL_CONTENT})
    assert res.get("success") is True, res
    target = profile_home(fleet, "trt") / "skills" / "routed-skill" / "SKILL.md"
    assert target.is_file(), f"routed create did not land under owner home: {target}"


# ---------------------------------------------------------------------------
# A2 — trt + create, owner=trt → approve; plain create under trt
# ---------------------------------------------------------------------------


def test_A2_owner_equals_active_plain_create(gate, fleet, enabled_config):
    switch_profile("trt", fleet)
    d = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
    assert not hd.is_block(d), f"own-profile create must not be denied: {d!r}"

    res = core_skill_manage("create", "routed-skill", OWNER_ROUTED_SKILL_CONTENT)
    assert res["success"] is True, res
    landed = profile_home(fleet, "trt") / "skills" / "routed-skill" / "SKILL.md"
    assert landed.is_file(), "plain create did not land under active profile home"


# ---------------------------------------------------------------------------
# A3 — growth + create, owner=trt → DENY sideways
# ---------------------------------------------------------------------------


def test_A3_named_profile_cannot_write_sideways(gate, fleet, enabled_config):
    switch_profile("growth", fleet)
    d = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
    assert hd.is_block(d), f"sideways create must be denied: {d!r}"
    msg = hd.block_message(d)
    assert MSG_SIDEWAYS in msg, f"sideways contract missing: {msg!r}"
    assert MSG_HANDOFF in msg, f"handoff contract missing: {msg!r}"


# ---------------------------------------------------------------------------
# A3b — growth + edit/delete/archive on trt-owned skill → DENY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["edit", "delete", "archive"])
def test_A3b_sideways_mutation_denied(gate, fleet, enabled_config, action):
    make_skill(profile_home(fleet, "trt"), "trt-owned", owner="trt")
    # prime the name→home index the way the daily watchdog would
    hd.call_scan(hd.get_drift_scan(), fleet)
    switch_profile("growth", fleet)
    before = snapshot_tree(fleet)
    d = decide(gate, action, name="trt-owned", content="# mutated\n")
    assert hd.is_block(d), f"sideways {action} must be denied: {d!r}"
    assert snapshot_tree(fleet) == before, "denied mutation changed fleet state"


# ---------------------------------------------------------------------------
# A3c — default + edit/delete/archive on trt-owned → allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["edit", "delete", "archive"])
def test_A3c_default_may_maintain_specialist_skill(gate, fleet, enabled_config, action):
    make_skill(profile_home(fleet, "trt"), "trt-owned", owner="trt")
    hd.call_scan(hd.get_drift_scan(), fleet)
    switch_profile("default", fleet)
    d = decide(gate, action, name="trt-owned", content="# maintained\n")
    assert not hd.is_block(d), f"default must be allowed to maintain (A3c): {d!r}"


# ---------------------------------------------------------------------------
# A3d — specialist maintains OWN skill → allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["edit", "delete", "archive"])
def test_A3d_owner_may_maintain_own_skill(gate, fleet, enabled_config, action):
    make_skill(profile_home(fleet, "growth"), "growth-owned", owner="growth")
    hd.call_scan(hd.get_drift_scan(), fleet)
    switch_profile("growth", fleet)
    d = decide(gate, action, name="growth-owned", content="# maintained\n")
    assert not hd.is_block(d), f"owner must maintain own skill (A3d): {d!r}"


# ---------------------------------------------------------------------------
# A4 — create w/o owner, require=true → DENY + guidance
# ---------------------------------------------------------------------------


def test_A4_missing_owner_metadata_denied(gate, fleet, enabled_config):
    switch_profile("default", fleet)
    d = decide(gate, "create", name="orphan-skill", content=VALID_SKILL_CONTENT)
    assert hd.is_block(d), f"missing owner must be denied under require=true: {d!r}"
    assert MSG_REQUIRE_METADATA_SHORT in hd.block_message(d)


# ---------------------------------------------------------------------------
# A5 — owner=nonexistent profile → DENY + "not registered"
# ---------------------------------------------------------------------------


def test_A5_unknown_owner_denied(gate, fleet, enabled_config):
    switch_profile("default", fleet)
    content = routed_content("missing-profile", "ghost-skill")
    d = decide(gate, "create", name="ghost-skill", content=content)
    assert hd.is_block(d), f"unknown owner must be denied: {d!r}"
    assert MSG_NOT_REGISTERED in hd.block_message(d)


# ---------------------------------------------------------------------------
# A6 — invalid owner ids → DENY at validation (before any FS lookup)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_owner", ["../../tmp", "root", "bad/profile"])
def test_A6_invalid_owner_denied_at_validation(gate, fleet, enabled_config, bad_owner):
    switch_profile("default", fleet)
    content = routed_content(bad_owner, "evil-skill")
    before = snapshot_tree(fleet)
    d = decide(gate, "create", name="evil-skill", content=content)
    assert hd.is_block(d), f"invalid owner {bad_owner!r} must be denied: {d!r}"
    assert MSG_RESOLVE_OWNER in hd.block_message(d)
    assert snapshot_tree(fleet) == before, "payload reached the filesystem"


# ---------------------------------------------------------------------------
# A6b — path-traversal / yaml-injection payloads never reach a path join/FS write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "../../tmp",                      # plain traversal
        "bad/profile",                    # separator
        "trt/../../default",              # mid-string traversal
        "${HOME}/x",                      # expansion probe
        "trt\nenabled: false",            # yaml injection: multiline value
        "trt: true",                      # yaml injection: colon payload
    ],
)
def test_A6b_injection_payloads_never_reach_fs(gate, fleet, enabled_config, payload):
    switch_profile("default", fleet)
    content = routed_content(payload, "inject-skill")
    before = snapshot_tree(fleet)
    d = decide(gate, "create", name="inject-skill", content=content)
    assert hd.is_block(d), f"injection payload {payload!r} must be denied: {d!r}"
    after = snapshot_tree(fleet)
    assert after == before, f"payload {payload!r} mutated the filesystem"


# ---------------------------------------------------------------------------
# A7 — policy read from DEFAULT config; specialist cannot weaken the rule
# ---------------------------------------------------------------------------


def test_A7_policy_read_from_default_home(gate, fleet):
    # fleet rule ON in the DEFAULT home config …
    write_default_config(fleet, enabled=True, require_owner_metadata=True, route_from_default=True)
    # … and the specialist tries to weaken it in its OWN config
    growth_cfg = profile_home(fleet, "growth") / "config.yaml"
    growth_cfg.parent.mkdir(parents=True, exist_ok=True)
    growth_cfg.write_text(
        "skills:\n  owner_routing:\n    enabled: false\n", encoding="utf-8"
    )
    switch_profile("growth", fleet)
    d = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
    assert hd.is_block(d), "specialist weakening own config must NOT weaken fleet rule"


# ---------------------------------------------------------------------------
# A7b — core enforces (simulated) → plugin create-gate DORMANT, logged once
# ---------------------------------------------------------------------------


def test_A7b_dormant_when_core_enforces(gate, fleet, enabled_config, monkeypatch):
    import tools.skill_manager_tool as smt

    def _core_policy():
        return {"enabled": True, "require_owner_metadata": True, "route_from_default": True}

    # simulation of merged PR #87101: the exact symbol SPEC-1 feature-detects
    monkeypatch.setattr(
        smt, "_skill_owner_routing_policy", _core_policy, raising=False
    )

    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    root_logger = logging.getLogger()
    old_level = root_logger.level
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)
    try:
        switch_profile("growth", fleet)
        for _ in range(3):
            d = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
            assert not hd.is_block(d), f"create-gate must be dormant while core enforces: {d!r}"
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(old_level)

    dormant_msgs = [m for m in records if "dormant" in m.lower() or "dormancy" in m.lower()]
    assert dormant_msgs, "dormancy must be logged (audit trail)"
    assert len(dormant_msgs) == 1, f"dormancy must be logged ONCE, not per-call: {dormant_msgs}"


# ---------------------------------------------------------------------------
# A8 — enabled: false explicit → full bypass incl. drifted legacy skills
# ---------------------------------------------------------------------------


def test_A8_explicit_disable_is_full_bypass(gate, fleet):
    write_default_config(fleet, enabled=False)
    # legacy drift: a trt-owned skill sitting in growth
    make_skill(profile_home(fleet, "growth"), "legacy-drift", owner="trt")
    hd.call_scan(hd.get_drift_scan(), fleet)
    switch_profile("growth", fleet)
    d_create = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
    assert not hd.is_block(d_create), "disabled must bypass create gate"
    d_edit = decide(gate, "edit", name="legacy-drift", content="# x\n")
    assert not hd.is_block(d_edit), "disabled must bypass mutation gate incl. drifted skills"


# ---------------------------------------------------------------------------
# A8b — key absent + plugin installed → ENABLED (default-ON posture)
# ---------------------------------------------------------------------------


def test_A8b_key_absent_means_enabled(gate, fleet):
    write_default_config(fleet)  # no skills.owner_routing subtree at all
    switch_profile("default", fleet)
    d = decide(gate, "create", name="orphan-skill", content=VALID_SKILL_CONTENT)
    assert hd.is_block(d), "key absent must mean ENABLED (default-ON posture)"
    assert MSG_REQUIRE_METADATA_SHORT in hd.block_message(d)


# ---------------------------------------------------------------------------
# A8c — route_from_default: false → no deny, no route; stays under default
# NOTE: SPEC-3 v1.0 pins refusal-posture (no deny). Upstream e12d79edd1 DENIES
# with "refuse cross-profile creation" — deliberate plugin divergence, see
# qa/matrix.md notes.
# ---------------------------------------------------------------------------


def test_A8c_route_from_default_false_stays_on_default(gate, fleet):
    write_default_config(fleet, enabled=True, require_owner_metadata=True, route_from_default=False)
    switch_profile("default", fleet)
    d = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
    assert not hd.is_block(d), "refusal posture must not deny the create (SPEC-3 A8c)"

    res = core_skill_manage("create", "routed-skill", OWNER_ROUTED_SKILL_CONTENT)
    assert res["success"] is True, res
    stayed = fleet / "skills" / "routed-skill" / "SKILL.md"
    routed = profile_home(fleet, "trt") / "skills" / "routed-skill" / "SKILL.md"
    assert stayed.is_file(), "plain create must stay under default home"
    assert not routed.exists(), "no routing may occur under route_from_default=false"


# ---------------------------------------------------------------------------
# A8d — require_owner_metadata: false → unowned skill allowed
# ---------------------------------------------------------------------------


def test_A8d_optional_metadata_allows_unowned_create(gate, fleet):
    write_default_config(fleet, enabled=True, require_owner_metadata=False)
    switch_profile("default", fleet)
    d = decide(gate, "create", name="a8d-skill", content=VALID_SKILL_CONTENT)
    assert not hd.is_block(d), "require_owner_metadata=false must allow ownerless create"

    res = core_skill_manage("create", "a8d-skill", VALID_SKILL_CONTENT)
    assert res["success"] is True, res
    assert (fleet / "skills" / "a8d-skill" / "SKILL.md").is_file()

    # unowned-state surfacing (V1 row state is the dashboard surface; the
    # engine-side observable is the scan knowing the skill is unowned)
    findings = hd.call_scan(hd.get_drift_scan(), fleet)
    own = [f for f in findings if f.get("skill") == "a8d-skill"]
    for f in own:
        hd.assert_valid_finding(f)
        assert f["kind"] in ("unowned", "unjustified-global"), (
            f"unowned skill surfaced as unexpected kind: {f['kind']} "
            "(SPEC-0 enum vs SPEC-3 B1c label divergence — see matrix notes)"
        )


# ---------------------------------------------------------------------------
# A8e — default + create, owner=default → plain create under default
# ---------------------------------------------------------------------------


def test_A8e_owner_default_is_not_a_route(gate, fleet, enabled_config):
    switch_profile("default", fleet)
    content = routed_content("default", "plain-skill")
    d = decide(gate, "create", name="plain-skill", content=content)
    assert not hd.is_block(d), "owner==default is not a route and must not deny"

    res = core_skill_manage("create", "plain-skill", content)
    assert res["success"] is True, res
    assert (fleet / "skills" / "plain-skill" / "SKILL.md").is_file(), (
        "owner=default create must land under default home"
    )


# ---------------------------------------------------------------------------
# A9 — latency p95 <50ms over N≥1000 non-skill_manage calls; early-bail zero I/O
# ---------------------------------------------------------------------------


def test_A9_latency_p95_and_zero_io_early_bail(gate, fleet, enabled_config, monkeypatch):
    args = {"command": "ls -la", "timeout": 60}

    # warm-up (lazy imports etc. happen here, not inside the measured/I-O-bombed passes)
    hd.call_hook(gate, NON_SKILL_TOOL, args)

    durations = []
    for _ in range(1100):
        t0 = time.perf_counter()
        d = hd.call_hook(gate, NON_SKILL_TOOL, args)
        durations.append((time.perf_counter() - t0) * 1000.0)
        assert not hd.is_block(d), "non-skill_manage calls must never be blocked"

    durations.sort()
    p95 = durations[int(0.95 * len(durations))]
    assert p95 < 50.0, f"p95 decision latency {p95:.2f}ms exceeds 50ms budget"

    # zero-I/O proof: bomb every common filesystem entrypoint and re-run —
    # the early-bail path must not touch any of them
    def _bomb(*a, **k):
        raise AssertionError("early-bail performed filesystem I/O")

    monkeypatch.setattr(builtins, "open", _bomb)
    monkeypatch.setattr(os, "stat", _bomb)
    monkeypatch.setattr(os, "lstat", _bomb)
    monkeypatch.setattr(os, "listdir", _bomb)
    monkeypatch.setattr(os, "scandir", _bomb)
    monkeypatch.setattr(Path, "exists", lambda self: (_ for _ in ()).throw(_bomb()))
    monkeypatch.setattr(Path, "is_file", lambda self: (_ for _ in ()).throw(_bomb()))
    monkeypatch.setattr(Path, "is_dir", lambda self: (_ for _ in ()).throw(_bomb()))
    for _ in range(100):
        d = hd.call_hook(gate, NON_SKILL_TOOL, args)
        assert not hd.is_block(d)


# ---------------------------------------------------------------------------
# A9b — routed-create round trip: write lands under owner home, ledger+usage ok
# ---------------------------------------------------------------------------


def test_A9b_routed_create_round_trip(gate, fleet, enabled_config):
    switch_profile("default", fleet)

    # deny-redirect first: plain create is refused, message hands off
    d = decide(gate, "create", content=OWNER_ROUTED_SKILL_CONTENT)
    assert hd.is_block(d)
    assert MSG_HANDOFF in hd.block_message(d)

    tool = hd.get_routed_create()
    res = hd.call_tool(tool, {"name": "routed-skill", "content": OWNER_ROUTED_SKILL_CONTENT})
    assert res.get("success") is True, res
    assert res.get("owner_profile") == "trt", res

    trt_home = profile_home(fleet, "trt")
    target_skill = trt_home / "skills" / "routed-skill" / "SKILL.md"
    default_skill = fleet / "skills" / "routed-skill" / "SKILL.md"
    target_usage = trt_home / "skills" / ".usage.json"
    default_usage = fleet / "skills" / ".usage.json"

    assert target_skill.is_file()
    assert not default_skill.exists()

    assert target_usage.is_file(), "usage record must land under owner home"
    usage = json.loads(target_usage.read_text())
    assert "routed-skill" in usage
    assert usage["routed-skill"].get("state") == "active"
    if default_usage.exists():
        assert "routed-skill" not in json.loads(default_usage.read_text())

    ledger = trt_home / "skills" / ".curator_ledger.jsonl"
    assert ledger.is_file(), "ledger record must land under owner home"
    creates = [
        json.loads(line)
        for line in ledger.read_text().splitlines()
        if line.strip()
    ]
    mine = [e for e in creates if e.get("skill") == "routed-skill"]
    assert mine, "no ledger entry for routed create"
    for entry in mine:
        for after in entry.get("after", []):
            assert str(after.get("path", "")).startswith(str(trt_home)), (
                f"ledger path escaped owner home: {after.get('path')}"
            )
