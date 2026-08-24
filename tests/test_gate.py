"""Gate decision tests — SPEC-3 Gate A matrix.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

import time

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
