"""Shared helpers: DEFAULT-home resolution + core-sys import indirection.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

All core imports are funnelled through ``core`` so unit tests can monkeypatch
one place instead of chasing hermes_cli/hermes_constants modules that only
exist inside a real Hermes checkout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_CACHE: dict[str, Any] = {}


def fleet_default_home() -> Path:
    """Return the default fleet HERMES_HOME from any profile-scoped home."""
    from hermes_constants import get_hermes_home

    current = get_hermes_home()
    try:
        resolved = current.resolve()
    except OSError:
        resolved = current
    if resolved.parent.name == "profiles":
        return resolved.parent.parent
    return resolved


def reset_caches() -> None:
    """Clear mtime caches (policy + dormancy + index). Test hook."""
    from . import policy as _policy
    from . import coexistence as _coexistence
    from . import index as _index

    _policy._CACHE.clear()
    _coexistence._DORMANT_STATE.clear()
    _coexistence._CORE_PROBE_CACHE.clear()
    _index.clear()


def utc_now_iso() -> str:
    """UTC timestamp, second resolution, ISO-8601 Z suffix."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_finding_id(kind: str, skill: str, scope: str, path: str = "") -> str:
    """Stable deterministic finding id: same drift ⇒ same id across rescans.

    The digest includes the skill's RESOLVED path so two distinct physical
    copies of the same skill name (twin findings) get distinct ids — while
    symlink aliases of one physical file resolve to the same path and
    therefore keep sharing one id.
    """
    import hashlib

    resolved = ""
    if path:
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = str(path)
    digest = hashlib.sha256(
        f"{kind}|{scope}|{skill}|{resolved}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{kind}-{digest}"
