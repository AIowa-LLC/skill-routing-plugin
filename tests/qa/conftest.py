"""QA-owned fixtures for the Gate A/B harness (tests/qa/).

Deliberately SHADOWS the canonical tests/conftest.py `fleet` fixture for
QA tests only: the developer fixture monkeypatches
``hermes_cli.profiles.get_active_profile_name`` to "default", which would
defeat Gate A rows that rely on REAL active-profile inference (A3, A7).
This fixture only points HERMES_HOME; profile context is switched per-test
via tests.qa.contracts.switch_profile — the real inference path.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CORE = Path("/home/tony/.hermes/hermes-agent")


@pytest.fixture()
def fleet(tmp_path):
    """Temp fleet root (Path) with HERMES_HOME at the DEFAULT home."""
    from qa.contracts import make_fleet

    saved = os.environ.get("HERMES_HOME")
    root = make_fleet(tmp_path)
    try:
        from skill_owner_routing import common

        common.reset_caches()
        yield root
    finally:
        from skill_owner_routing import common

        common.reset_caches()
        if saved is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved


@pytest.fixture(scope="session")
def core_root() -> Path:
    if not (CORE / "hermes_constants.py").is_file():
        pytest.skip("hermes core checkout not found")
    return CORE


@pytest.fixture(scope="session")
def core_commit() -> str:
    out = subprocess.run(
        ["git", "-C", str(CORE), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()
