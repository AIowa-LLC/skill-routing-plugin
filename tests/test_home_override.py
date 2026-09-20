"""dashboard/home_override unit tests — the M7 env-fallback no-op fix.

The context manager pins engine + policy reads to a specific fleet home.
Primary mechanism: the core token override (set_hermes_home_override).
Fallback: swapping the HERMES_HOME env var — the only lever on hosts without
the token machinery. OCR M7: the fallback silently no-oped when the token
machinery was unavailable AND HERMES_HOME was not already set (the
``prev_env is not None`` guard), so engine calls resolved the REAL fleet
home — exactly the wrong-fleet read this module exists to prevent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboard"))

import home_override  # noqa: E402


def _hermes_home() -> Path:
    import hermes_constants

    return hermes_constants.get_hermes_home()


class TestEnvFallbackPinsHome:
    """M7 — fallback path must pin HERMES_HOME even when it was unset."""

    def test_fallback_pins_home_when_env_previously_unset(self, tmp_path, monkeypatch):
        home = tmp_path / "fleet-home"
        home.mkdir()
        monkeypatch.delenv("HERMES_HOME", raising=False)

        def broken_set(*_a, **_k):
            raise RuntimeError("token machinery unavailable")

        monkeypatch.setattr(
            "hermes_constants.set_hermes_home_override", broken_set
        )

        with home_override._home_override(home):
            # Inside the window the engine resolves OUR home, not the real
            # fleet home (the wrong-fleet read M7 describes).
            assert os.environ.get("HERMES_HOME") == str(home)
            assert _hermes_home() == home

        # Exit restores the pre-entry state exactly — previously-unset
        # means unset again, not a leaked override.
        assert "HERMES_HOME" not in os.environ

    def test_fallback_restores_previous_env_value(self, tmp_path, monkeypatch):
        home = tmp_path / "fleet-home"
        home.mkdir()
        other = tmp_path / "other-home"
        other.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(other))

        def broken_set(*_a, **_k):
            raise RuntimeError("token machinery unavailable")

        monkeypatch.setattr(
            "hermes_constants.set_hermes_home_override", broken_set
        )

        with home_override._home_override(home):
            assert os.environ["HERMES_HOME"] == str(home)
        assert os.environ["HERMES_HOME"] == str(other)

    def test_token_path_leaves_env_untouched(self, tmp_path, monkeypatch):
        """Primary path: env var is not a lever at all when tokens work."""
        import hermes_constants

        home = tmp_path / "fleet-home"
        home.mkdir()
        monkeypatch.delenv("HERMES_HOME", raising=False)

        with home_override._home_override(home):
            assert "HERMES_HOME" not in os.environ
            assert hermes_constants.get_hermes_home_override() == str(home)
        assert hermes_constants.get_hermes_home_override() is None
