"""Skip logic for tests that require the BUILD-1 engine.

Every Gate A/B test imports :func:`engine` which raises a friendly skip
when the engine modules have not landed yet — so the suite is green-but-
skipped while BUILD-1 is in flight and turns on automatically the moment
the engine lands, with zero test edits.
"""

from __future__ import annotations

import importlib

import pytest

from qa.contracts import ENGINE_MODULES, ensure_engine_importable, engine_present, plugin_root


def _load():
    if not engine_present():
        pytest.skip(
            f"BUILD-1 engine not present at {plugin_root() / 'skill_owner_routing'} "
            "(gate.py missing) — harness ready, will run when engine lands"
        )
    ensure_engine_importable()
    mods = {}
    for name in ENGINE_MODULES:
        try:
            mods[name] = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — surfaced in the failure
            pytest.fail(f"engine module {name} failed to import: {exc}")
    return mods


def engine():
    """Return dict of loaded engine modules, or pytest.skip when absent."""
    return _load()


def pytest_collection_modifyitems(config, items):
    """Auto-skip Gate A/B tests when the engine is absent so a green run is
    never mistaken for coverage. Skip reason is loud and self-explaining."""
    if engine_present():
        return
    skip = pytest.mark.skip(reason="BUILD-1 engine not present — harness ready, awaiting engine")
    for item in items:
        mod = item.module.__name__
        if mod.startswith("qa.test_gate_a") or mod.startswith("qa.test_gate_b"):
            item.add_marker(skip)
