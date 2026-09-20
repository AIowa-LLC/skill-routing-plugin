"""OCR v0.2.1 minors — Group E: dashboard/home_override residuals (M4/M7 module).

- A failed token reset leaks the process-global override silently: now
  logged + best-effort outright clear.
- _engine_call converts every failure to silent None: now each branch
  logs why (missing submodule, missing attr, raising call).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboard"))

import home_override  # noqa: E402


class TestResetFailure:
    def test_failed_reset_logs_and_clears(self, tmp_path, monkeypatch, caplog):
        """The token reset failing must be loud AND attempt cleanup — a
        stuck override pins every later engine read to the wrong home."""
        import hermes_constants

        home = tmp_path / "fleet-home"
        home.mkdir()
        real_set = hermes_constants.set_hermes_home_override

        def broken_reset(_token):
            raise RuntimeError("reset machinery broken")

        monkeypatch.setattr(
            hermes_constants, "reset_hermes_home_override", broken_reset
        )
        with caplog.at_level(logging.ERROR, logger="home_override"):
            with home_override._home_override(home):
                assert hermes_constants.get_hermes_home_override() == str(home)
            # leaving the context with a broken reset:
        assert any(
            "FAILED to reset" in r.message for r in caplog.records
        ), "override leak must be logged"
        # best-effort cleanup cleared the stuck override
        assert hermes_constants.get_hermes_home_override() is None

    def test_fallback_token_unavailable_logs(self, tmp_path, monkeypatch, caplog):
        """M7 env fallback entry is the designed degraded path — its
        exception is logged (diagnosable) and the override still works."""
        home = tmp_path / "fleet-home"
        home.mkdir()
        monkeypatch.delenv("HERMES_HOME", raising=False)

        def broken_set(*_a, **_k):
            raise RuntimeError("token machinery unavailable")

        monkeypatch.setattr(
            "hermes_constants.set_hermes_home_override", broken_set
        )
        with caplog.at_level(logging.ERROR, logger="home_override"):
            with home_override._home_override(home):
                import os

                assert os.environ["HERMES_HOME"] == str(home)
        assert any(
            "token machinery unavailable" in r.message for r in caplog.records
        )
        assert "HERMES_HOME" not in __import__("os").environ


class TestEngineCallLogging:
    def test_missing_submodule_logs(self, tmp_path, caplog):
        module = {"drift": __import__("types").ModuleType("fake")}
        with caplog.at_level(logging.ERROR, logger="home_override"):
            out = home_override._engine_call(
                module, "scan", tmp_path, submodule="ledger"
            )
        assert out is None
        assert any("no 'ledger' submodule" in r.message for r in caplog.records)

    def test_missing_attr_logs(self, tmp_path, caplog):
        module = {"drift": __import__("types").ModuleType("fake")}
        with caplog.at_level(logging.ERROR, logger="home_override"):
            out = home_override._engine_call(
                module, "no_such_fn", tmp_path, submodule="drift"
            )
        assert out is None
        assert any("no callable 'no_such_fn'" in r.message for r in caplog.records)

    def test_raising_call_logs(self, tmp_path, caplog):
        import types

        fake = types.ModuleType("fake")
        fake.scan = lambda: (_ for _ in ()).throw(RuntimeError("engine blew up"))
        module = {"drift": fake}
        with caplog.at_level(logging.ERROR, logger="home_override"):
            out = home_override._engine_call(
                module, "scan", tmp_path, submodule="drift"
            )
        assert out is None
        assert any(
            "engine call drift.scan() raised" in r.message for r in caplog.records
        )

    def test_successful_call_still_returns(self, tmp_path):
        import types

        fake = types.ModuleType("fake")
        fake.scan = lambda: {"ok": True}
        module = {"drift": fake}
        assert home_override._engine_call(
            module, "scan", tmp_path, submodule="drift"
        ) == {"ok": True}
