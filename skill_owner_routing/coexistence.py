"""Core-coexistence dormancy probe.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Feature-detect per create-decision (SPEC-1): core symbol
``tools.skill_manager_tool._skill_owner_routing_policy`` present AND core
policy enabled from DEFAULT-home config ⇒ plugin CREATE gate goes DORMANT
(log once, audit-only). Dormancy is scoped to the create gate ONLY — edit/
patch/delete/write_file/remove_file/archive gating NEVER dorms, because
PR #87101 gates create exclusively. Never double-deny.
"""

from __future__ import annotations

from typing import Any, Dict

# module_name -> {"dormant": bool, "logged": bool}
_DORMANT_STATE: Dict[str, Any] = {}
_CORE_PROBE_CACHE: Dict[str, Any] = {}
_PROBE_TTL_SECONDS = 30.0


def core_symbol_present() -> bool:
    """True when the exact symbol PR #87101 introduces exists in core."""
    try:
        import tools.skill_manager_tool as smt  # type: ignore[import-not-found]
    except Exception:
        return False
    return getattr(smt, "_skill_owner_routing_policy", None) is not None


def core_policy_enabled() -> bool:
    """Resolve core's own policy (absent key = disabled in core semantics).

    Reuses core's reader when present so we read exactly what core enforces;
    the mtime-cached DEFAULT-home config read covers the rest.
    """
    from .common import fleet_default_home

    try:
        import tools.skill_manager_tool as smt  # type: ignore[import-not-found]

        reader = getattr(smt, "_skill_owner_routing_policy", None)
        if reader is None:
            return False
        core_policy = reader()
        if isinstance(core_policy, dict) and core_policy.get("enabled"):
            return True
        # Partial-core fail-safe (SPEC-3 D5b): symbol exists but its policy
        # says disabled — core is NOT enforcing; plugin must stay active.
        return False
    except Exception:
        del fleet_default_home  # probe is best-effort; never raises
        return False


def create_gate_dormant() -> bool:
    """Decide create-gate dormancy with a short-lived probe cache.

    Both conditions true → dormant. The probe cache is time-boxed (not
    permanent) so a core downgrade reactivates the plugin gate on the next
    decision — no zombie dormancy (SPEC-3 D5c).
    """
    import time

    now = time.monotonic()
    cached = _CORE_PROBE_CACHE.get("probe")
    if cached is not None and (now - cached[0]) < _PROBE_TTL_SECONDS:
        return cached[1]
    dormant = core_symbol_present() and core_policy_enabled()
    _CORE_PROBE_CACHE["probe"] = (now, dormant)
    state = _DORMANT_STATE.setdefault(
        "create", {"dormant": dormant, "logged": False}
    )
    state["dormant"] = dormant
    return dormant


def mark_dormancy_logged() -> bool:
    """Return True exactly once per dormancy epoch (log-once contract)."""
    state = _DORMANT_STATE.setdefault("create", {"dormant": False, "logged": False})
    if state["logged"]:
        return False
    state["logged"] = True
    return True
