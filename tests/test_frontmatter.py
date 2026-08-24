"""Frontmatter parse/validate tests (SPEC-3 A6/A6b).

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from conftest import OWNER_ROUTED_SKILL_CONTENT
from skill_owner_routing.frontmatter import declared_skill_owner, resolve_owner_identity


class TestDeclaredSkillOwner:
    def test_parses_owner_from_metadata(self):
        assert declared_skill_owner(OWNER_ROUTED_SKILL_CONTENT) == "trt"

    def test_missing_metadata_returns_none(self):
        content = "---\nname: x\ndescription: y\n---\n\nBody\n"
        assert declared_skill_owner(content) is None

    def test_empty_owner_returns_none(self):
        content = (
            "---\nname: x\ndescription: y\nmetadata:\n  hermes:\n"
            "    owner_profile: ''\n---\n\nBody\n"
        )
        assert declared_skill_owner(content) is None

    def test_owner_is_lowercased(self):
        content = (
            "---\nname: x\ndescription: y\nmetadata:\n  hermes:\n"
            "    owner_profile: TRT\n---\n\nBody\n"
        )
        assert declared_skill_owner(content) == "trt"

    def test_garbage_content_returns_none(self):
        assert declared_skill_owner("") is None
        assert declared_skill_owner("no frontmatter at all") is None

    def test_fallback_parser_without_core(self, monkeypatch):
        # Simulate core's parse_frontmatter being unavailable
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "agent.skill_utils":
                raise ImportError("core unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert declared_skill_owner(OWNER_ROUTED_SKILL_CONTENT) == "trt"


class TestResolveOwnerIdentity:
    def test_valid_owner_resolves(self):
        owner, err = resolve_owner_identity("trt")
        assert err is None
        assert owner == "trt"

    def test_default_is_valid(self):
        owner, err = resolve_owner_identity("default")
        assert err is None
        assert owner == "default"

    def test_mixed_case_normalizes(self):
        owner, err = resolve_owner_identity("TRT")
        assert err is None
        assert owner == "trt"

    def test_path_traversal_rejected_before_fs(self, monkeypatch):
        # A6/A6b: validate runs BEFORE any FS lookup — profile_exists must
        # never see a traversal payload.
        def boom(*args, **kwargs):
            raise AssertionError("invalid owner reached profile lookup")

        import hermes_cli.profiles as profiles_mod

        monkeypatch.setattr(profiles_mod, "profile_exists", boom)
        for bad in ["../../tmp", "bad/profile", "..", "a/../b"]:
            owner, err = resolve_owner_identity(bad)
            assert owner is None, bad
            assert "Could not resolve skill owner profile" in err, bad

    def test_reserved_names_rejected(self):
        for reserved in ["root", "hermes", "test", "tmp", "sudo"]:
            owner, err = resolve_owner_identity(reserved)
            assert owner is None, reserved
