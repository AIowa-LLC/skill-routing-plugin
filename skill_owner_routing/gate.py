"""pre_tool_call decision engine — the enforcement surface.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

ONE hook registration on pre_tool_call. Early-bail (zero I/O) when
tool_name != "skill_manage". Action-switched over the full mutation set:
create, edit, patch, delete, write_file, remove_file, archive.

Directives: ``block`` (deterministic deny, PR #87101 message contract).
NO ``approve``-escalate for sideways writes — a human ``[a]lways`` on the
approval gate would permanently weaken a fleet rule.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GATED_TOOL = "skill_manage"
MUTATION_ACTIONS = {"edit", "patch", "delete", "write_file", "remove_file", "archive"}
CREATE_ACTION = "create"


def pre_tool_call(**kwargs: Any) -> Optional[Dict[str, str]]:
    """Hook entry: return {"action": "block", "message": ...} or None."""
    tool_name = str(kwargs.get("tool_name") or "")
    if tool_name != GATED_TOOL:  # zero-I/O early bail
        return None
    try:
        return _decide(kwargs)
    except Exception as exc:
        # FAIL-CLOSED (2026-09-12 ruling + SECURITY-CONTRACT §Gate): an
        # unexpected gate error on a skill_manage call must block the
        # mutation, never silently allow it. The message is deliberately
        # distinct from a policy violation; only the exception CLASS is
        # exposed, never its text or inputs.
        logger.exception("skill-owner-routing gate error (fail-closed)")
        return _block(
            f"skill-owner-routing gate error (fail-closed): the routing gate "
            f"could not evaluate this skill_manage call "
            f"({type(exc).__name__}) and blocked it to be safe. This is a "
            f"gate malfunction, not a policy violation. Please retry the "
            f"call; if it fails again, report this with the log traceback.",
            error_code="gate-error",
        )


def _decide(kwargs: Dict[str, Any]) -> Optional[Dict[str, str]]:
    raw_args = kwargs.get("args")
    args = raw_args if isinstance(raw_args, dict) else {}
    action = str(args.get("action") or "")
    name = str(args.get("name") or "")

    if action == CREATE_ACTION:
        return _gate_create(args, name)
    if action in MUTATION_ACTIONS:
        return _gate_mutation(action, name)
    return None


# ── create gate ───────────────────────────────────────────────────────────


def _gate_create(args: Dict[str, Any], name: str) -> Optional[Dict[str, str]]:
    from . import coexistence, policy as policy_mod
    from .frontmatter import declared_skill_owner, resolve_owner_identity

    pol = policy_mod.read_policy()
    if not pol.get("enabled"):
        return None  # explicit user-disable: full bypass

    # Core coexistence: if core already enforces create routing, plugin gate
    # goes DORMANT (audit-only, log once). Edit/delete gating NEVER dorms.
    if coexistence.create_gate_dormant():
        if coexistence.mark_dormancy_logged():
            logger.info(
                "skill-owner-routing: core create-gating detected "
                "(PR #87101 semantics present + enabled); plugin create gate "
                "DORMANT (audit-only). Sideways-mutation gating remains active."
            )
        return None

    content = str(args.get("content") or "")
    owner = declared_skill_owner(content)
    if not owner:
        if pol.get("require_owner_metadata", True):
            return _block(
                "Skill owner routing is enabled. New skills must declare "
                "metadata.hermes.owner_profile in SKILL.md frontmatter. "
                "Choose the profile that owns the capability's primary "
                "output, not the profile that happened to discover it."
            )
        return None

    owner, err = resolve_owner_identity(owner)
    if err is not None:
        return _block(err)

    # FAIL-CLOSED (2026-09-19 ruling, OCR M1 + cross-check item 5): the
    # old "cannot resolve actor — do not invent a denial" posture let any
    # actor-resolution failure silently bypass the whole gate. Reversed:
    # an unresolvable actor is a gate malfunction and blocks.
    try:
        active = _active_profile()
    except Exception as exc:
        logger.exception(
            "skill-owner-routing gate error (fail-closed): "
            "could not resolve the active profile"
        )
        return _block(
            f"skill-owner-routing gate error (fail-closed): the routing gate "
            f"could not resolve the active profile for this skill_manage "
            f"call ({type(exc).__name__}) and blocked it to be safe. This "
            f"is a gate malfunction, not a policy violation. Please retry "
            f"the call; if it fails again, report this with the log "
            f"traceback.",
            error_code="gate-error",
        )

    from .routed_create import _profile_exists

    if not _profile_exists(owner):
        return _block(
            f"Declared skill owner profile {owner!r} is not registered."
        )

    if active == owner or (active == "default" and owner == "default"):
        return None  # vanilla path

    if active != "default":
        return _block(
            f"Skill {name!r} declares owner_profile={owner!r}, but the active "
            f"profile is {active!r}. Named specialists may not write skills "
            "sideways into another specialist. Hand the skill creation to "
            f"the {owner!r} profile."
        )

    # active == default, owner is a named specialist → deny plain create and
    # redirect to the plugin tool that performs the routed transaction.
    if pol.get("route_from_default", True):
        return _block(
            f"Skill {name!r} belongs to owner profile {owner!r}. Default must "
            f"not create it locally. Hand the skill creation to the "
            f"skill_owner_create tool (name={name!r}, content with "
            f"metadata.hermes.owner_profile={owner!r}) to run the routed "
            f"create into {owner!r}'s home."
        )
    return _block(
        f"Skill {name!r} belongs to {owner!r}. Default owner-routing is "
        "configured to refuse cross-profile creation; delegate the create."
    )


# ── mutation gate (NEVER dormant) ────────────────────────────────────────


def _gate_mutation(action: str, name: str) -> Optional[Dict[str, str]]:
    from . import policy as policy_mod
    from .frontmatter import declared_skill_owner
    from .index import lookup

    pol = policy_mod.read_policy()
    if not pol.get("enabled"):
        return None  # A8: explicit disable = full bypass incl. drifted legacy

    if not name:
        return None

    # FAIL-CLOSED (2026-09-19 ruling, OCR M1): an unresolvable actor is a
    # gate malfunction and blocks — never a silent allow.
    try:
        active = _active_profile()
    except Exception as exc:
        logger.exception(
            "skill-owner-routing gate error (fail-closed): "
            "could not resolve the active profile"
        )
        return _block(
            f"skill-owner-routing gate error (fail-closed): the routing gate "
            f"could not resolve the active profile for this skill_manage "
            f"call ({type(exc).__name__}) and blocked it to be safe. This "
            f"is a gate malfunction, not a policy violation. Please retry "
            f"the call; if it fails again, report this with the log "
            f"traceback.",
            error_code="gate-error",
        )

    skill_md = lookup(name)
    if skill_md is None:
        return None  # unresolvable target: let core's own not-found path run

    # FAIL-CLOSED (2026-09-19 ruling, OCR M2): a target whose location
    # cannot be resolved (ELOOP, permissions) is a gate malfunction and
    # blocks — the old posture converted resolve() failures to a silent
    # allow, contradicting the fail-closed contract.
    try:
        location_scope = _scope_of(skill_md)
    except OSError as exc:
        logger.exception(
            "skill-owner-routing gate error (fail-closed): "
            "could not resolve target skill location"
        )
        return _block(
            f"skill-owner-routing gate error (fail-closed): the routing gate "
            f"could not resolve where the target skill lives to evaluate "
            f"this {action} ({type(exc).__name__}) and blocked it to be "
            f"safe. This is a gate malfunction, not a policy violation. "
            f"Please retry the call; if it fails again, report this with "
            f"the log traceback.",
            error_code="gate-error",
        )
    if location_scope is None:
        return None

    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # FAIL-CLOSED (SECURITY-CONTRACT §Gate): unreadable required input on
        # a skill_manage mutation must block, never silently allow.
        logger.exception("skill-owner-routing gate error (fail-closed)")
        return _block(
            f"skill-owner-routing gate error (fail-closed): the routing gate "
            f"could not read the target skill to evaluate this {action} "
            f"and blocked it to be safe. This is a gate malfunction, not a "
            f"policy violation. Please retry the call; if it fails again, "
            f"report this with the log traceback.",
            error_code="gate-error",
        )

    owner = declared_skill_owner(content)

    # Sideways rule: a named specialist may only mutate skills located in its
    # own home (or skills it owns). Default may maintain anything (A3c).
    if active == "default":
        return None
    if location_scope == active:
        return None
    if owner is not None and owner == active:
        return None

    target_desc = f"owner_profile={owner!r}" if owner else "no owner_profile"
    return _block(
        f"Cannot {action} skill {name!r}: it lives in profile "
        f"{location_scope!r} ({target_desc}), but the active profile is "
        f"{active!r}. Named specialists may not mutate another profile's "
        f"skill sideways. Hand the {action} to the {location_scope!r} profile"
        + (
            f" (or default)."
            if owner is None
            else "."
        )
    )


def _scope_of(skill_md) -> "str | None":
    """Map a SKILL.md path to its profile scope ('default' or profile name).

    Raises OSError when path resolution itself fails (ELOOP, permission
    errors) — the caller treats that as a gate malfunction and blocks
    (2026-09-19 ruling, OCR M2). A resolvable path outside both known
    roots still returns None.
    """
    from .common import fleet_default_home

    resolved = skill_md.resolve()
    default_home = fleet_default_home().resolve()
    try:
        if resolved.is_relative_to(default_home / "profiles"):
            rest = resolved.relative_to(default_home / "profiles")
            if not rest.parts:  # <default_home>/profiles itself is no scope
                return None
            return rest.parts[0]
    except ValueError:
        pass
    try:
        if resolved.is_relative_to(default_home):
            return "default"
    except ValueError:
        pass
    return None


def _active_profile() -> str:
    """Resolve the actor, failing CLOSED on any resolution failure.

    Returns the normalized active profile name. Raises on any failure —
    hermes_cli.profiles import, get_active_profile_name(), or identity
    normalization — and callers must convert that into a gate-malfunction
    block, never an allow (2026-09-19 ruling, OCR M1; reversed from the
    old return-None fail-open posture).
    """
    from hermes_cli.profiles import get_active_profile_name

    raw = get_active_profile_name() or "default"
    from .frontmatter import resolve_owner_identity

    normalized, err = resolve_owner_identity(raw)
    if err is not None or not normalized:
        raise ValueError("active profile name did not validate")
    return normalized


def _block(message: str, error_code: str = "policy-violation") -> Dict[str, str]:
    """Block directive. ``error_code`` distinguishes policy violations
    (default) from gate malfunctions ("gate-error") for consumers that read
    it; the host hook schema currently consumes only action/message and
    tolerates the extra key (forward-compatible)."""
    return {"action": "block", "message": message, "error_code": error_code}
