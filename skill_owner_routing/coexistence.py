"""Core-coexistence dormancy probe.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Feature-detect per create-decision (SPEC-1): core symbol
``tools.skill_manager_tool._skill_owner_routing_policy`` present AND core
policy enabled from DEFAULT-home config ⇒ plugin CREATE gate goes DORMANT
(log once, audit-only). Dormancy is scoped to the create gate ONLY — edit/
patch/delete/write_file/remove_file/archive gating NEVER dorms, because
PR #87101 gates create exclusively. Never double-deny.

HISTORICAL (Tony ruling 2026-09-12): current core carries no owner-routing
symbols at all, so this probe always returns False there and the plugin is
FULLY ACTIVE BY DESIGN — the accepted posture on a single-user fleet (the
gate is the product). The probe remains for any future core that
reintroduces PR #87101 enforcement.
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
        # Probe is best-effort; never raises. (The historical
        # ``del fleet_default_home`` here was a lint-silencing no-op tied
        # to a dead import — both removed.)
        return False


def create_gate_dormant() -> bool:
    """Decide create-gate dormancy with a short-lived probe cache.

    Both conditions true → dormant. The probe cache is time-boxed (not
    permanent) so a core downgrade reactivates the plugin gate on the next
    decision — no zombie dormancy (SPEC-3 D5c).

    A dormancy EPOCH FLIP (active→dormant or dormant→active) resets the
    log-once flag: the old code left ``logged`` True forever, so the
    second dormancy epoch was never announced — the gate silently went
    dormant again with zero log lines.
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
    if state["dormant"] != dormant:
        # epoch flip: re-arm the log-once announcement for the new epoch
        state["logged"] = False
    state["dormant"] = dormant
    return dormant


def mark_dormancy_logged() -> bool:
    """Return True exactly once per dormancy epoch (log-once contract)."""
    state = _DORMANT_STATE.setdefault("create", {"dormant": False, "logged": False})
    if state["logged"]:
        return False
    state["logged"] = True
    return True
