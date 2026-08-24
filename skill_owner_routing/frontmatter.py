"""owner_profile frontmatter parse + validation.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

from typing import Optional, Tuple


def declared_skill_owner(content: str) -> Optional[str]:
    """Return metadata.hermes.owner_profile from SKILL.md frontmatter."""
    try:
        from agent.skill_utils import parse_frontmatter

        frontmatter, _body = parse_frontmatter(content)
    except Exception:
        frontmatter = _fallback_parse(content)
    metadata = frontmatter.get("metadata") if isinstance(frontmatter, dict) else None
    if not isinstance(metadata, dict):
        return None
    hermes_meta = metadata.get("hermes")
    if not isinstance(hermes_meta, dict):
        return None
    raw = hermes_meta.get("owner_profile")
    if raw is None:
        return None
    owner = str(raw).strip().lower()
    return owner or None


def _fallback_parse(content: str) -> dict:
    """Minimal metadata.hermes.owner_profile extraction when core util is
    unavailable (e.g. plugin exercised outside a Hermes checkout)."""
    if not content.startswith("---"):
        return {}
    import re

    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return {}
    block = content[3 : end.start() + 3]
    owner = None
    in_hermes = False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("hermes:"):
            in_hermes = True
            continue
        if stripped and not line[0].isspace():
            in_hermes = False
            continue
        if in_hermes and stripped.startswith("owner_profile:"):
            owner = stripped.split(":", 1)[1].strip()
    if owner is None:
        return {}
    return {"metadata": {"hermes": {"owner_profile": owner}}}


def resolve_owner_identity(owner: str) -> Tuple[Optional[str], Optional[str]]:
    """Normalize + validate an owner id BEFORE any filesystem lookup.

    Returns ``(normalized_owner, error)`` — exactly one is None. Validation
    (validate_profile_name) runs before profile_exists touches the FS, so a
    traversal payload never reaches a path join.
    """
    try:
        from hermes_cli.profiles import normalize_profile_name, validate_profile_name

        normalized = normalize_profile_name(owner)
        validate_profile_name(normalized)
    except Exception as exc:
        return None, f"Could not resolve skill owner profile {owner!r}: {exc}"
    return normalized, None
