"""Routed-create transaction tests (SPEC-3 A1/A9b) — real FS round trip.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

import json

from conftest import OWNER_ROUTED_SKILL_CONTENT, set_active_profile


def routed(name, content, **kwargs):
    from skill_owner_routing.routed_create import routed_create

    return json.loads(routed_create(name=name, content=content, **kwargs))


class TestRoutedCreate:
    def test_default_routes_create_into_owner_home(self, enabled_config):
        # A1/A9b: write lands under trt home; ledger+usage scoped there too
        result = routed("routed-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is True, result
        assert result["owner_profile"] == "trt"
        assert result["routed_from_profile"] == "default"
        assert "hand those mutations" in result["hint"]

        trt_skill = fleet_skill_path("trt", "routed-skill")
        assert trt_skill.is_file()
        assert not fleet_skill_path("default", "routed-skill").exists()
        trt_usage = enabled_config["trt"] / "skills" / ".usage.json"
        default_usage = enabled_config["root"] / "skills" / ".usage.json"
        if trt_usage.exists():
            assert "routed-skill" in trt_usage.read_text(encoding="utf-8")
        if default_usage.exists():
            assert "routed-skill" not in default_usage.read_text(encoding="utf-8")

    def test_owner_equals_active_creates_locally(self, enabled_config, monkeypatch):
        set_active_profile(monkeypatch, enabled_config, "trt")
        # NOTE: core's plain create writes into the CALLER's home (HERMES_HOME),
        # which in production IS the owner's home when that profile is active.
        # In this fixture HERMES_HOME stays at the fleet root, so the file
        # lands there — matching core e12d79edd1 semantics exactly.
        result = routed("local-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is True
        assert result.get("owner_profile") == "trt"
        assert "routed_from_profile" not in result
        assert fleet_skill_path("default", "local-skill").is_file()

    def test_sideways_create_denied(self, enabled_config, monkeypatch):
        set_active_profile(monkeypatch, enabled_config, "growth")
        result = routed("routed-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is False
        assert result["owner_profile"] == "trt"
        assert result["active_profile"] == "growth"
        assert "may not write skills sideways" in result["error"]
        assert not fleet_skill_path("trt", "routed-skill").exists()

    def test_missing_owner_denied_when_required(self, enabled_config):
        result = routed("plain-skill", "---\nname: plain\ndescription: x.\n---\nBody\n")
        assert result["success"] is False
        assert "owner_profile" in result["error"]

    def test_unknown_owner_denied(self, enabled_config):
        content = OWNER_ROUTED_SKILL_CONTENT.replace(
            "owner_profile: trt", "owner_profile: missing-profile"
        )
        result = routed("routed-skill", content)
        assert result["success"] is False
        assert "is not registered" in result["error"]

    def test_invalid_owner_denied_before_fs(self, enabled_config, monkeypatch):
        import hermes_cli.profiles as profiles_mod

        monkeypatch.setattr(
            profiles_mod,
            "profile_exists",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("invalid owner reached profile lookup")
            ),
        )
        content = OWNER_ROUTED_SKILL_CONTENT.replace(
            "owner_profile: trt", "owner_profile: ../../tmp"
        )
        result = routed("routed-skill", content)
        assert result["success"] is False
        assert "Could not resolve skill owner profile" in result["error"]

    def test_refusal_posture_when_route_from_default_false(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n"
            "  owner_routing:\n"
            "    enabled: true\n"
            "    route_from_default: false\n",
            encoding="utf-8",
        )
        result = routed("routed-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is False
        assert "refuse cross-profile creation" in result["error"]
        assert not fleet_skill_path("trt", "routed-skill").exists()

    def test_disabled_policy_vanilla_create(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing:\n    enabled: false\n", encoding="utf-8"
        )
        result = routed("plain-skill", "---\nname: plain\ndescription: x.\n---\nBody\n")
        assert result["success"] is True, result
        assert fleet_skill_path("default", "plain-skill").is_file()

    def test_owner_metadata_optional_when_configured(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n"
            "  owner_routing:\n"
            "    enabled: true\n"
            "    require_owner_metadata: false\n",
            encoding="utf-8",
        )
        result = routed("plain-skill", "---\nname: plain\ndescription: x.\n---\nBody\n")
        assert result["success"] is True
        assert fleet_skill_path("default", "plain-skill").is_file()

    def test_route_restores_caller_home_after_failure(self, enabled_config):
        # simulate create failure (duplicate name) — override must unwind
        first = routed("dup-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert first["success"] is True
        second = routed("dup-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert second["success"] is False
        import hermes_constants

        assert str(hermes_constants.get_hermes_home()) == str(enabled_config["root"])


def fleet_skill_path(scope: str, name: str):
    home = {"default": None, "trt": "trt", "growth": "growth"}[scope]
    import os

    root = __import__("pathlib").Path(os.environ["HERMES_HOME"])
    if home:
        root = root / "profiles" / home
    return root / "skills" / name / "SKILL.md"
