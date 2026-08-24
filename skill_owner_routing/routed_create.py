"""Routed-create transaction — ported faithfully from core commit e12d79edd1.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Mechanism (public primitives only, zero core edits):
``set_hermes_home_override(get_profile_dir(owner))`` around the vanilla
``skill_manage(action='create')`` call, with post-write bookkeeping (ledger
+ usage) held inside the same override so records land in the OWNER home,
not the caller's.
"""

from __future__ import annotations

import json
from typing import Any, Dict


def routed_create(
    name: str,
    content: str,
    category: "str | None" = None,
    task_id: "str | None" = None,
    session_id: "str | None" = None,
) -> str:
    """Create a skill via the owner-routing transaction. Returns JSON string.

    Decision flow (mirrors _create_skill_with_owner_routing from e12d79edd1):
    policy off → plain create; missing owner + require → deny w/ guidance;
    unknown/invalid owner → deny; owner == active → plain create; sideways
    (active is a named specialist ≠ owner) → deny hand-off; default +
    route_from_default → scoped override create in owner home.
    """
    from . import policy as policy_mod
    from .frontmatter import declared_skill_owner, resolve_owner_identity

    pol = policy_mod.read_policy()
    if not pol.get("enabled"):
        return _plain_create(name, content, category, task_id, session_id)

    owner = declared_skill_owner(content)
    if not owner:
        if pol.get("require_owner_metadata", True):
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        "Skill owner routing is enabled. New skills must declare "
                        "metadata.hermes.owner_profile in SKILL.md frontmatter. "
                        "Choose the profile that owns the capability's primary "
                        "output, not the profile that happened to discover it."
                    ),
                },
                ensure_ascii=False,
            )
        return _plain_create(name, content, category, task_id, session_id)

    owner, err = resolve_owner_identity(owner)
    if err is not None:
        return json.dumps(
            {"success": False, "error": err}, ensure_ascii=False
        )

    active = _active_profile()
    if active is None:
        return json.dumps(
            {"success": False, "error": "Could not resolve the active profile."},
            ensure_ascii=False,
        )

    if not _profile_exists(owner):
        return json.dumps(
            {
                "success": False,
                "error": f"Declared skill owner profile {owner!r} is not registered.",
            },
            ensure_ascii=False,
        )

    if active == owner:
        result_json = _plain_create(name, content, category, task_id, session_id)
        try:
            result = json.loads(result_json)
        except Exception:
            return result_json
        if result.get("success"):
            result["owner_profile"] = owner
        return json.dumps(result, ensure_ascii=False)

    if active != "default":
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"Skill {name!r} declares owner_profile={owner!r}, but the active "
                    f"profile is {active!r}. Named specialists may not write skills "
                    "sideways into another specialist. Hand the skill creation to "
                    f"the {owner!r} profile."
                ),
                "owner_profile": owner,
                "active_profile": active,
            },
            ensure_ascii=False,
        )

    if not pol.get("route_from_default", True):
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"Skill {name!r} belongs to {owner!r}. Default owner-routing is "
                    "configured to refuse cross-profile creation; delegate the create."
                ),
                "owner_profile": owner,
            },
            ensure_ascii=False,
        )

    return _scoped_create_in_owner(
        name, content, category, owner, task_id, session_id
    )


def _plain_create(
    name: str,
    content: str,
    category: "str | None",
    task_id: "str | None" = None,
    session_id: "str | None" = None,
) -> str:
    from tools.skill_manager_tool import skill_manage

    return skill_manage(
        action="create",
        name=name,
        content=content,
        category=category,
        task_id=task_id,
        session_id=session_id,
    )


def _active_profile() -> "str | None":
    try:
        from hermes_cli.profiles import get_active_profile_name

        raw = get_active_profile_name() or "default"
        from .frontmatter import resolve_owner_identity

        normalized, err = resolve_owner_identity(raw)
        if err is not None:
            return raw.strip().lower() or None
        return normalized
    except Exception:
        return None


def _profile_exists(owner: str) -> bool:
    try:
        from hermes_cli.profiles import profile_exists

        return bool(profile_exists(owner))
    except Exception:
        return False


def _scoped_create_in_owner(
    name: str,
    content: str,
    category: "str | None",
    owner: str,
    task_id: "str | None" = None,
    session_id: "str | None" = None,
) -> str:
    """The routed transaction: scope HERMES_HOME to the owner for BOTH the
    create and the post-write bookkeeping, then restore. Ported from
    e12d79edd1 (create + post-write token scope)."""
    import logging

    logger = logging.getLogger(__name__)
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    from hermes_cli.profiles import get_profile_dir

    target_home = get_profile_dir(owner)
    token = set_hermes_home_override(target_home)
    try:
        result_json = _plain_create(name, content, category, task_id, session_id)
        try:
            result = json.loads(result_json)
        except Exception:
            return result_json
        if not result.get("success"):
            return result_json
        result["owner_profile"] = owner
        result["routed_from_profile"] = "default"
        result["message"] = (
            f"Skill {name!r} created in owner profile {owner!r} "
            "(routed from default)."
        )
        result["hint"] = (
            f"Further edits, patches, or supporting-file writes for {name!r} "
            f"belong to profile {owner!r}; hand those mutations to that profile."
        )
        logger.info(
            "skill-owner-routing: routed create of %r into owner profile %r",
            name,
            owner,
        )
        return json.dumps(result, ensure_ascii=False)
    finally:
        reset_hermes_home_override(token)
