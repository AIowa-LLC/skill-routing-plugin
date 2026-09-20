"""owner_profile frontmatter parse + validation.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def declared_skill_owner(content: str) -> Optional[str]:
    """Return metadata.hermes.owner_profile from SKILL.md frontmatter."""
    try:
        from agent.skill_utils import parse_frontmatter
    except ImportError:
        # Expected degraded environment (plugin exercised outside a
        # Hermes checkout) — the fallback parser is the designed path.
        frontmatter = _fallback_parse(content)
    except Exception:
        # A real parser malfunction is NOT expected: log it so the
        # degraded classification is visible, then fall back (same
        # result, but diagnosable instead of silent).
        logger.exception(
            "skill-owner-routing: core frontmatter parser raised; using "
            "the fallback parser for this skill"
        )
        frontmatter = _fallback_parse(content)
    else:
        try:
            frontmatter, _body = parse_frontmatter(content)
        except Exception:
            logger.exception(
                "skill-owner-routing: core frontmatter parse failed; using "
                "the fallback parser for this skill"
            )
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
    unavailable (e.g. plugin exercised outside a Hermes checkout).

    Tracks the enclosing top-level key so ``hermes:`` is honored ONLY
    under ``metadata:`` — the old line-scanner matched ``hermes:`` at any
    depth, so a ``defaults:`` block declaring ``hermes.owner_profile``
    was read as the skill's owner. Values keep only their content: a
    matching pair of surrounding quotes is stripped. The closing fence
    may sit at EOF without a trailing newline.
    """
    if not content.startswith("---"):
        return {}
    import re

    end = re.search(r"\n---[ \t]*(?:\r?\n|$)", content[3:])
    if not end:
        return {}
    block = content[3 : end.start() + 3]
    owner = None
    parent: "str | None" = None  # last top-level (column-0) key seen
    hermes_indent: "int | None" = None  # indentation of the tracked hermes: key
    for line in block.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            parent = stripped.split(":", 1)[0]
            hermes_indent = None  # a new top-level key closes the window
            continue
        if parent == "metadata" and stripped.startswith("hermes:"):
            hermes_indent = indent
            continue
        if (
            hermes_indent is not None
            and indent > hermes_indent
            and stripped.startswith("owner_profile:")
        ):
            owner = stripped.split(":", 1)[1].strip()
            if len(owner) >= 2 and owner[0] == owner[-1] and owner[0] in "\"'":
                owner = owner[1:-1]
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
    except ImportError:
        # Environment failure, not a validation verdict — say so. The old
        # message ("Could not resolve skill owner profile …") read like a
        # rejection of the OWNER when the environment simply cannot
        # validate anything.
        return None, (
            f"Cannot validate skill owner profile {owner!r}: "
            "hermes_cli.profiles is not importable in this environment."
        )
    try:
        normalized = normalize_profile_name(owner)
        validate_profile_name(normalized)
    except Exception as exc:
        return None, f"Could not resolve skill owner profile {owner!r}: {exc}"
    return normalized, None
