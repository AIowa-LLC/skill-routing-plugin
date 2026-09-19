"""Gate decision tests — SPEC-3 Gate A matrix.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

import logging
import time
from pathlib import Path

from conftest import (
    OWNER_ROUTED_SKILL_CONTENT,
    VALID_NO_OWNER_CONTENT,
    set_active_profile,
    write_skill,
)


def decide(**kwargs):
    from skill_owner_routing.gate import pre_tool_call

    return pre_tool_call(**kwargs)


def call(action, name="routed-skill", content=None, **extra):
    args = {"action": action, "name": name}
    if content is not None:
        args["content"] = content
    args.update(extra)
    return decide(tool_name="skill_manage", args=args)


class TestEarlyBail:
    def test_non_skill_manage_tools_pass_through(self):
        assert decide(tool_name="terminal", args={"command": "rm -rf /"}) is None
        assert decide(tool_name="read_file", args={"path": "/etc"}) is None
        assert decide(tool_name="", args={}) is None

    def test_non_skill_manage_is_zero_io(self, fleet, monkeypatch):
        # A9: early-bail must do NO filesystem/config I/O
        def fail(*args, **kwargs):
            raise AssertionError("early-bail path performed I/O")

        import builtins

        monkeypatch.setattr("pathlib.Path.stat", fail)
        monkeypatch.setattr("pathlib.Path.exists", fail)
        monkeypatch.setattr(builtins, "open", fail)
        assert decide(tool_name="web_search", args={"query": "x"}) is None
        assert decide(tool_name="read_file", args={"path": "y"}) is None

    def test_decision_latency_p95_under_50ms(self):
        # A9: p95 <50ms over N>=1000 non-skill_manage calls
        samples = []
        for i in range(1000):
            start = time.perf_counter()
            decide(tool_name="terminal", args={"command": f"echo {i}"})
            samples.append((time.perf_counter() - start) * 1000.0)
        samples.sort()
        p95 = samples[int(0.95 * len(samples)) - 1]
        assert p95 < 50.0, f"p95={p95}ms"

    def test_unknown_action_passes(self, enabled_config):
        assert call("list") is None
        assert call("view", name="x") is None


class TestCreateGate:
    def test_missing_owner_metadata_denied_with_guidance(self, enabled_config):
        # A4: DENY + guidance message naming owner_profile
        result = call("create", content=VALID_NO_OWNER_CONTENT)
        assert result["action"] == "block"
        assert "owner_profile" in result["message"]

    def test_missing_owner_allowed_when_not_required(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n"
            "  owner_routing:\n"
            "    enabled: true\n"
            "    require_owner_metadata: false\n",
            encoding="utf-8",
        )
        # A8d: allowed (surfaces later as unowned drift finding)
        assert call("create", content=VALID_NO_OWNER_CONTENT) is None

    def test_disabled_bypasses_everything(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing:\n    enabled: false\n", encoding="utf-8"
        )
        assert call("create", content=VALID_NO_OWNER_CONTENT) is None

    def test_owner_equals_active_passes(self, fleet, enabled_config, monkeypatch):
        set_active_profile(monkeypatch, fleet, "trt")
        assert call("create", content=OWNER_ROUTED_SKILL_CONTENT) is None

    def test_owner_is_default_from_default_passes(self, fleet, enabled_config):
        content = OWNER_ROUTED_SKILL_CONTENT.replace(
            "owner_profile: trt", "owner_profile: default"
        )
        assert call("create", content=content) is None  # A8e

    def test_named_specialist_sideways_create_denied(self, fleet, enabled_config, monkeypatch):
        # A3: growth + create owner=trt → DENY w/ hand-off message
        set_active_profile(monkeypatch, fleet, "growth")
        result = call("create", content=OWNER_ROUTED_SKILL_CONTENT)
        assert result is not None and result["action"] == "block"
        assert "may not write skills sideways" in result["message"]
        assert "Hand the skill creation to" in result["message"]
        assert "trt" in result["message"]

    def test_default_create_for_named_owner_redirects(self, fleet, enabled_config):
        # active==default + owner=named → DENY plain create, redirect to tool
        result = call("create", content=OWNER_ROUTED_SKILL_CONTENT)
        assert result is not None and result["action"] == "block"
        assert "skill_owner_create" in result["message"]

    def test_default_refusal_posture_when_routing_disabled(self, fleet):
        # A8c: route_from_default false → deny with delegate message
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n"
            "  owner_routing:\n"
            "    enabled: true\n"
            "    route_from_default: false\n",
            encoding="utf-8",
        )
        result = call("create", content=OWNER_ROUTED_SKILL_CONTENT)
        assert result["action"] == "block"
        assert "refuse cross-profile creation" in result["message"]

    def test_unknown_owner_denied_not_registered(self, fleet, enabled_config):
        content = OWNER_ROUTED_SKILL_CONTENT.replace(
            "owner_profile: trt", "owner_profile: missing-profile"
        )
        result = call("create", content=content)
        assert result is not None and result["action"] == "block"
        assert "is not registered" in result["message"]

    def test_invalid_owner_denied_before_fs_lookup(self, fleet, enabled_config, monkeypatch):
        # A6/A6b
        import hermes_cli.profiles as profiles_mod

        monkeypatch.setattr(
            profiles_mod,
            "profile_exists",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("invalid owner reached profile lookup")
            ),
        )
        for bad in ["../../tmp", "root", "bad/profile"]:
            content = OWNER_ROUTED_SKILL_CONTENT.replace(
                "owner_profile: trt", f"owner_profile: {bad}"
            )
            result = call("create", content=content)
            assert result is not None and result["action"] == "block", bad
            assert "Could not resolve skill owner profile" in result["message"], bad


class TestMutationGate:
    def test_specialist_edit_own_skill_allowed(self, fleet, enabled_config, monkeypatch):
        set_active_profile(monkeypatch, fleet, "trt")
        write_skill(fleet["trt"], "trt-skill", owner="trt")
        assert call("edit", name="trt-skill", content="x") is None  # A3d

    def test_specialist_edit_sideways_denied(self, fleet, enabled_config, monkeypatch):
        # A3b: growth + edit on trt-owned skill → DENY
        set_active_profile(monkeypatch, fleet, "growth")
        write_skill(fleet["trt"], "trt-skill", owner="trt")
        result = call("edit", name="trt-skill", content="x")
        assert result is not None and result["action"] == "block"
        assert "may not mutate another profile's skill sideways" in result["message"]

    def test_default_edit_specialist_skill_allowed(self, fleet, enabled_config):
        # A3c: default may maintain anything
        write_skill(fleet["trt"], "trt-skill", owner="trt")
        for action in ["edit", "patch", "delete", "write_file", "remove_file", "archive"]:
            extra = (
                {"file_path": "references/x.md", "file_content": "y"}
                if action == "write_file"
                else {"file_path": "references/x.md"}
                if action == "remove_file"
                else {"old_string": "a", "new_string": "b"}
                if action == "patch"
                else {"content": "z"}
                if action == "edit"
                else {}
            )
            assert call(action, name="trt-skill", **extra) is None, action

    def test_specialist_delete_own_unowned_scope_skill_allowed(
        self, fleet, enabled_config, monkeypatch
    ):
        # skill physically in trt home without owner metadata: trt may edit
        set_active_profile(monkeypatch, fleet, "trt")
        write_skill(fleet["trt"], "legacy-skill")
        assert call("delete", name="legacy-skill") is None

    def test_specialist_cannot_mutate_global_skill_sideways(
        self, fleet, enabled_config, monkeypatch
    ):
        set_active_profile(monkeypatch, fleet, "growth")
        write_skill(fleet["root"], "global-skill")
        result = call("delete", name="global-skill")
        assert result is not None and result["action"] == "block"

    def test_mutation_gate_never_dorms_with_core_present(
        self, fleet, enabled_config, monkeypatch
    ):
        # D5c complement: even when create-gate dormancy fires, mutations gate
        import tools.skill_manager_tool as smt

        monkeypatch.setattr(
            smt, "_skill_owner_routing_policy", lambda: {"enabled": True}, raising=False
        )
        from skill_owner_routing import coexistence, common

        common.reset_caches()
        assert coexistence.create_gate_dormant() is True
        set_active_profile(monkeypatch, fleet, "growth")
        write_skill(fleet["trt"], "trt-skill", owner="trt")
        result = call("edit", name="trt-skill", content="x")
        assert result is not None and result["action"] == "block"

    def test_disabled_policy_bypasses_mutation_gate(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing:\n    enabled: false\n", encoding="utf-8"
        )
        write_skill(fleet["trt"], "trt-skill", owner="trt")  # A8: incl. legacy drift
        from skill_owner_routing import common

        common.reset_caches()
        import hermes_cli.profiles as profiles_mod

        original = profiles_mod.get_active_profile_name
        profiles_mod.get_active_profile_name = lambda: "growth"
        try:
            assert call("delete", name="trt-skill") is None
        finally:
            profiles_mod.get_active_profile_name = original

    def test_unresolvable_target_passes(self, fleet, enabled_config):
        assert call("delete", name="no-such-skill-anywhere") is None

    def test_index_staleness_falls_back_to_scan(self, fleet, enabled_config, monkeypatch):
        # index hit validated by Path.exists; stale entry → bounded rescan
        write_skill(fleet["trt"], "moved-skill", owner="trt")
        from skill_owner_routing import index

        index.update_index(
            {"moved-skill": str(fleet["trt"] / "skills" / "moved-skill" / "SKILL.md")}
        )
        assert index.lookup("moved-skill") is not None
        index.update_index(
            {"moved-skill": str(fleet["trt"] / "skills" / "gone" / "SKILL.md")}
        )
        found = index.lookup("moved-skill")  # stale → fallback scan finds real
        assert found is not None
        assert found.parent.name == "moved-skill"

    # -- M3 (OCR review @ d1659fb): traversal containment in the index -------

    def test_traversal_shaped_names_resolve_to_none(self, fleet, enabled_config):
        # `name` reaches lookup() straight from skill_manage args; it must
        # never be joined into a path that can escape the skills roots. The
        # decoys prove the payloads WOULD hit on a naive join: base returns
        # hermes/SKILL.md for ".." and skills/nested-target/SKILL.md for
        # "x/../nested-target".
        from skill_owner_routing import index

        (fleet["root"] / "SKILL.md").write_text("decoy\n", encoding="utf-8")
        write_skill(fleet["root"], "nested-target", owner="trt")
        for bad in ["..", "../..", "a/../b", "x/../nested-target", "."]:
            assert index.lookup(bad) is None, bad
            assert index._bounded_scan(bad) is None, bad
        assert index.lookup("nested-target") is not None  # plain name still works

    def test_scan_hit_outside_current_fleet_is_not_returned_or_cached(self, fleet, tmp_path):
        # Symmetric containment: a bounded-scan hit must pass the same
        # _within_current_fleet check the cache-hit path enforces before it
        # is returned or cached — an out-of-fleet file a scan could reach
        # (e.g. via a symlinked skills root) must resolve to None.
        from skill_owner_routing import index

        outside = tmp_path / "outside-fleet" / "escape" / "SKILL.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("---\nname: escape\n---\nbody\n", encoding="utf-8")

        fake_roots = [tmp_path / "outside-fleet"]
        original = index._profile_roots
        index._profile_roots = lambda: fake_roots
        try:
            assert index._within_current_fleet(outside) is False
            assert index.lookup("escape") is None
        finally:
            index._profile_roots = original
        with index._LOCK:
            assert "escape" not in index._INDEX  # never cached into the fleet index


# -- M1/M2 (OCR review @ d1659fb): fail-closed on actor/scope resolution ----
#
# Ruling 2026-09-19 (OCR M1/M2 + cross-check item 5): the fail-open
# "cannot resolve actor — do not invent a denial" posture is REVERSED —
# actor/scope-resolution failures are gate malfunctions and must block
# with a message labeled GATE MALFUNCTION, never leaking exception text.

GATE_MALFUNCTION_MARKERS = (
    "gate malfunction",  # the label consumers are told to look for
    "not a policy violation",  # never confused with a policy deny
    "fail-closed",  # posture marker shared with the M10 family
)


def _assert_malfunction_block(result):
    """A malfunction block: directive, label, error_code, no text leak."""
    assert result is not None and result["action"] == "block"
    msg = result["message"]
    for marker in GATE_MALFUNCTION_MARKERS:
        assert marker in msg, marker
    assert result["error_code"] == "gate-error"
    assert "sensitive-secret-boom" not in msg  # never leak exception text
    assert "resolution-boom" not in msg


class TestGateActorResolutionFailClosed:
    """M1 — _active_profile() failures must block (fail-closed), not allow."""

    def test_create_actor_resolution_failure_blocks_malfunction(
        self, fleet, enabled_config, monkeypatch, caplog
    ):
        import hermes_cli.profiles as profiles_mod

        def boom():
            raise RuntimeError("sensitive-secret-boom")

        monkeypatch.setattr(profiles_mod, "get_active_profile_name", boom)
        with caplog.at_level(logging.ERROR, logger="skill_owner_routing.gate"):
            result = call("create", content=OWNER_ROUTED_SKILL_CONTENT)
        _assert_malfunction_block(result)
        assert any(r.exc_info for r in caplog.records)  # full traceback logged

    def test_mutation_actor_resolution_failure_blocks_malfunction(
        self, fleet, enabled_config, monkeypatch, caplog
    ):
        import hermes_cli.profiles as profiles_mod

        write_skill(fleet["trt"], "trt-skill", owner="trt")
        set_active_profile(monkeypatch, fleet, "growth")

        def boom():
            raise RuntimeError("sensitive-secret-boom")

        monkeypatch.setattr(profiles_mod, "get_active_profile_name", boom)
        with caplog.at_level(logging.ERROR, logger="skill_owner_routing.gate"):
            result = call("edit", name="trt-skill", content="x")
        _assert_malfunction_block(result)
        assert any(r.exc_info for r in caplog.records)

    def test_actor_resolution_failure_never_touches_non_skill_tools(
        self, fleet, enabled_config, monkeypatch
    ):
        # Zero-I/O early bail: a broken actor resolver must not affect
        # anything but skill_manage.
        import hermes_cli.profiles as profiles_mod

        def boom():
            raise RuntimeError("sensitive-secret-boom")

        monkeypatch.setattr(profiles_mod, "get_active_profile_name", boom)
        assert decide(tool_name="terminal", args={"command": "ls"}) is None
        assert decide(tool_name="read_file", args={"path": "/etc"}) is None

    def test_happy_path_create_still_allows(self, fleet, enabled_config, monkeypatch):
        # Control: no fault → owner-matching create passes.
        set_active_profile(monkeypatch, fleet, "trt")
        assert call("create", content=OWNER_ROUTED_SKILL_CONTENT) is None


class TestGateScopeResolutionFailClosed:
    """M2 — _scope_of() resolution failures must block, not allow."""

    def test_scope_resolve_oserror_blocks_malfunction(
        self, fleet, enabled_config, monkeypatch, caplog
    ):
        from skill_owner_routing import index

        skill_md = write_skill(fleet["trt"], "trt-skill", owner="trt")
        set_active_profile(monkeypatch, fleet, "growth")

        real_resolve = Path.resolve

        def boom(self, strict=False):
            if self.name == "SKILL.md":
                raise OSError("resolution-boom ELOOP")
            return real_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", boom)
        monkeypatch.setattr(index, "lookup", lambda name: skill_md)
        with caplog.at_level(logging.ERROR, logger="skill_owner_routing.gate"):
            result = call("edit", name="trt-skill", content="x")
        _assert_malfunction_block(result)
        assert any(r.exc_info for r in caplog.records)

    def test_happy_path_mutation_still_allows(self, fleet, enabled_config, monkeypatch):
        # Control: no fault → own-home mutation passes.
        set_active_profile(monkeypatch, fleet, "trt")
        write_skill(fleet["trt"], "own-skill", owner="trt")
        assert call("edit", name="own-skill", content="x") is None

    def test_scope_of_degenerate_profiles_dir_does_not_crash(self, fleet):
        # _scope_of(<default_home>/profiles) raised IndexError via an
        # unguarded rest.parts[0] (OCR minors, gate.py:215-220). Degenerate
        # paths must map to None (→ malfunction block at the call site),
        # never escape as an uncaught crash.
        from skill_owner_routing import gate

        assert gate._scope_of(fleet["root"] / "profiles") is None

