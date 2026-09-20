"""OCR v0.2.1 minors — Group C: frontmatter fallback parser (PROBED trio).

All three bugs were PROBED live against the base parser; each test
forces the fallback path (core parser import blocked) and pins the fix:

- ``hermes:`` matched under ANY top-level key — owner read out of a
  ``defaults:`` block (privilege-shape confusion).
- quoted values kept their quotes — owner='"trt"'.
- closing fence at EOF without a trailing newline — owner missed.

Plus the error-separation fixes:
- ImportError (expected degraded env) vs real parse errors (logged).
- resolve_owner_identity ImportError message says the ENVIRONMENT cannot
  validate, not that the owner was rejected.
"""

from __future__ import annotations

import builtins
import logging

import pytest

from conftest import OWNER_ROUTED_SKILL_CONTENT


@pytest.fixture()
def no_core_parser(monkeypatch):
    """Force the fallback parser: agent.skill_utils import fails."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "agent.skill_utils":
            raise ImportError("core unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def _owner_content(owner_line: str, top_key: str = "metadata") -> str:
    return (
        "---\n"
        "name: probe\n"
        "description: x.\n"
        f"{top_key}:\n"
        "  hermes:\n"
        f"    {owner_line}\n"
        "---\n\n"
        "body\n"
    )


class TestProbedFallbackTrio:
    def test_hermes_under_defaults_is_ignored(self, no_core_parser):
        """PROBED: ``hermes:`` under ``defaults:`` used to be matched —
        owner='evil' read from a non-ownership block."""
        from skill_owner_routing.frontmatter import declared_skill_owner

        content = (
            "---\n"
            "name: probe\n"
            "defaults:\n"
            "  hermes:\n"
            "    owner_profile: evil\n"
            "metadata:\n"
            "  hermes:\n"
            "    owner_profile: trt\n"
            "---\n\nbody\n"
        )
        assert declared_skill_owner(content) == "trt"

    def test_defaults_only_block_yields_no_owner(self, no_core_parser):
        from skill_owner_routing.frontmatter import declared_skill_owner

        content = (
            "---\n"
            "name: probe\n"
            "defaults:\n"
            "  hermes:\n"
            "    owner_profile: evil\n"
            "---\n\nbody\n"
        )
        assert declared_skill_owner(content) is None

    def test_quoted_value_loses_its_quotes(self, no_core_parser):
        """PROBED: owner='"trt"' (quotes kept) broke every downstream
        profile comparison."""
        from skill_owner_routing.frontmatter import declared_skill_owner

        assert declared_skill_owner(_owner_content('owner_profile: "trt"')) == "trt"
        assert declared_skill_owner(_owner_content("owner_profile: 'trt'")) == "trt"

    def test_eof_fence_without_trailing_newline(self, no_core_parser):
        """PROBED: closing fence at EOF (no trailing \n) — owner missed."""
        from skill_owner_routing.frontmatter import declared_skill_owner

        content = (
            "---\n"
            "name: probe\n"
            "metadata:\n"
            "  hermes:\n"
            "    owner_profile: trt\n"
            "---"  # EOF, no trailing newline
        )
        assert declared_skill_owner(content) == "trt"

    def test_plain_and_uppercase_owners_still_parse(self, no_core_parser):
        from skill_owner_routing.frontmatter import declared_skill_owner

        assert declared_skill_owner(OWNER_ROUTED_SKILL_CONTENT) == "trt"
        assert declared_skill_owner(_owner_content("owner_profile: TRT")) == "trt"

    def test_no_metadata_block_still_none(self, no_core_parser):
        from skill_owner_routing.frontmatter import declared_skill_owner

        assert declared_skill_owner("---\nname: x\ndescription: y\n---\nbody\n") is None


class TestErrorSeparation:
    def test_real_parser_error_is_logged_not_silent(self, monkeypatch, caplog):
        """Import succeeds but the parser raises: ImportError (expected,
        quiet) must be separated from real errors (logged)."""

        class BrokenParser:
            @staticmethod
            def parse_frontmatter(_content):
                raise RuntimeError("parser exploded")

        monkeypatch.setitem(
            __import__("sys").modules,
            "agent.skill_utils",
            BrokenParser,
        )
        from skill_owner_routing.frontmatter import declared_skill_owner

        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.frontmatter"):
            owner = declared_skill_owner(OWNER_ROUTED_SKILL_CONTENT)
        assert owner == "trt"  # fallback still rescues the value
        assert any(
            "parse failed" in r.message or "raised" in r.message
            for r in caplog.records
        ), "real parser errors must be logged"

    def test_import_error_stays_quiet(self, no_core_parser, caplog):
        from skill_owner_routing.frontmatter import declared_skill_owner

        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.frontmatter"):
            assert declared_skill_owner(OWNER_ROUTED_SKILL_CONTENT) == "trt"
        assert not [
            r for r in caplog.records if "parse failed" in r.message
        ], "expected degraded-env fallback must not spam"

    def test_resolve_owner_identity_import_error_names_environment(self, monkeypatch):
        """ImportError of hermes_cli.profiles is an ENV failure — the
        message must not read like the owner was rejected."""
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "hermes_cli.profiles":
                raise ImportError("no core")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        from skill_owner_routing.frontmatter import resolve_owner_identity

        owner, err = resolve_owner_identity("trt")
        assert owner is None
        assert err is not None
        assert "not importable" in err
        assert "Could not resolve skill owner profile" not in err
