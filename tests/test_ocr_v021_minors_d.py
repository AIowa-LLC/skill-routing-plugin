"""OCR v0.2.1 minors — Group D: routed_create tool contract (M1 family).

- Exceptions inside the override window must return a JSON error object,
  never propagate raw (register.py returns the value straight through to
  the tool caller — the contract is "returns JSON string").
- _active_profile / _profile_exists log their swallowed exceptions (an
  infra fault must not masquerade as a silent policy refusal).
- _active_profile no longer guesses a normalization when identity
  resolution fails — deny-on-unresolvable, same family as gate M1.
"""

from __future__ import annotations

import json
import logging

import pytest

from conftest import OWNER_ROUTED_SKILL_CONTENT, set_active_profile


def routed(name, content, **kwargs):
    from skill_owner_routing.routed_create import routed_create

    return json.loads(routed_create(name=name, content=content, **kwargs))


class TestOverrideWindowJsonContract:
    def test_mid_transaction_exception_returns_json_error(self, enabled_config, monkeypatch):
        """A create that raises inside the override window must surface
        as a JSON error object (tool contract), not a raw exception —
        and the override must still unwind."""
        from skill_owner_routing import routed_create as rc

        def exploding_create(*a, **k):
            raise RuntimeError("engine exploded mid-create")

        monkeypatch.setattr(rc, "_plain_create", exploding_create)
        result = routed("boom-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is False
        assert "mid-transaction" in result["error"]
        assert "RuntimeError" in result["error"]
        import hermes_constants

        assert str(hermes_constants.get_hermes_home()) == str(enabled_config["root"])

    def test_pre_window_exception_returns_json_error(self, enabled_config, monkeypatch):
        """set_hermes_home_override failing BEFORE the window opens also
        returns JSON (nothing was written)."""
        import hermes_constants

        def broken_set(_home):
            raise OSError("override machinery unavailable")

        monkeypatch.setattr(
            hermes_constants, "set_hermes_home_override", broken_set
        )
        result = routed("boom-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is False
        assert "before entering" in result["error"]
        assert not (enabled_config["trt"] / "skills" / "boom-skill").exists()

    def test_reset_failure_is_logged_not_silent(self, enabled_config, monkeypatch, caplog):
        """The finally-reset failing must be LOUD: the override would
        leak process-wide (and the leak is real — this test cleans it up
        so the suite stays hermetic)."""
        import hermes_constants
        from skill_owner_routing import routed_create as rc

        real_reset = hermes_constants.reset_hermes_home_override

        def broken_reset(_token):
            raise RuntimeError("token reset failed")

        monkeypatch.setattr(
            hermes_constants, "reset_hermes_home_override", broken_reset
        )
        try:
            with caplog.at_level(
                logging.ERROR, logger="skill_owner_routing.routed_create"
            ):
                result = routed("loud-skill", OWNER_ROUTED_SKILL_CONTENT)
            assert result["success"] is True  # the create itself succeeded
            assert any(
                "FAILED to reset" in r.message for r in caplog.records
            ), "override leak must be logged"
        finally:
            # the leak is genuine: clear the context-local override the
            # broken reset stranded, then prove it cleared
            hermes_constants.set_hermes_home_override(None)
            assert hermes_constants.get_hermes_home_override() is None
            assert str(hermes_constants.get_hermes_home()) == str(
                enabled_config["root"]
            )
            monkeypatch.setattr(
                hermes_constants, "reset_hermes_home_override", real_reset
            )


class TestSwallowLogging:
    def test_active_profile_import_failure_logs(self, enabled_config, monkeypatch, caplog):
        import builtins

        from skill_owner_routing import routed_create as rc

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "hermes_cli.profiles":
                raise ImportError("gone")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with caplog.at_level(logging.ERROR, logger="skill_owner_routing.routed_create"):
            assert rc._active_profile() is None
        assert any("active profile" in r.message for r in caplog.records)

    def test_profile_exists_failure_logs(self, enabled_config, monkeypatch, caplog):
        import hermes_cli.profiles as profiles_mod

        from skill_owner_routing import routed_create as rc

        def boom(owner):
            raise RuntimeError("registry unreadable")

        monkeypatch.setattr(profiles_mod, "profile_exists", boom)
        with caplog.at_level(logging.ERROR, logger="skill_owner_routing.routed_create"):
            assert rc._profile_exists("trt") is False
        assert any("profile_exists" in r.message for r in caplog.records)


class TestDenyOnUnresolvable:
    def test_unresolvable_active_profile_denies_instead_of_guessing(self, enabled_config, monkeypatch):
        """The old fallback guessed ``raw.strip().lower()`` when identity
        resolution failed — two normalization regimes compared against
        the owner. Now: deny with 'Could not resolve the active profile'."""
        import hermes_cli.profiles as profiles_mod

        # e.g. a future core returning a name core's own validator
        # rejects (whitespace-padded, mixed-case-with-symbols...)
        monkeypatch.setattr(
            profiles_mod, "get_active_profile_name", lambda: "bad profile"
        )
        result = routed("routed-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is False
        assert "Could not resolve the active profile" in result["error"]
        assert not (enabled_config["trt"] / "skills" / "routed-skill").exists()

    def test_normal_active_profile_still_routes(self, enabled_config, monkeypatch):
        set_active_profile(monkeypatch, enabled_config, "default")
        result = routed("ok-skill", OWNER_ROUTED_SKILL_CONTENT)
        assert result["success"] is True, result
        assert result["owner_profile"] == "trt"
