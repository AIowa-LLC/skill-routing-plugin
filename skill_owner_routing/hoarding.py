"""No-Specialist-Hoarding justification lint (Tony ruling: plugin owns it).

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Global-scope skills must justify their placement: control-plane /
shared-primitive / verified-structural-dependency. Unjustified → finding.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Accepted justification keys, checked in metadata.hermes.global_justification
# or a body marker. ONE source of truth: the set defines the vocabulary and
# the body-marker alternation is DERIVED from it (sorted for a stable
# pattern) — the two definitions can no longer diverge (OCR minor).
JUSTIFICATION_KEYS = {
    "control-plane",
    "shared-primitive",
    "verified-structural-dependency",
}

_BODY_MARKER = re.compile(
    r"global[- ]justification:\s*("
    + "|".join(sorted(JUSTIFICATION_KEYS))
    + r")(?![\-\w])",
    re.IGNORECASE,
)


def global_justification(frontmatter: Dict[str, Any], body: str) -> Optional[str]:
    """Return the validated justification class, or None when unjustified."""
    metadata = frontmatter.get("metadata")
    if isinstance(metadata, dict):
        hermes = metadata.get("hermes")
        if isinstance(hermes, dict):
            raw = hermes.get("global_justification")
            if isinstance(raw, str):
                key = raw.strip().lower()
                if key in JUSTIFICATION_KEYS:
                    return key
    match = _BODY_MARKER.search(body or "")
    if match:
        return match.group(1).lower()
    return None


def lint_global_skill(
    skill: str, frontmatter: Dict[str, Any], body: str, path: str = ""
) -> Optional[Dict[str, Any]]:
    """Return an `unowned`/hoarding finding dict for an unjustified global
    skill, or None when the skill carries a valid justification."""
    from .common import new_finding_id, utc_now_iso

    justification = global_justification(frontmatter, body)
    if justification is not None:
        return None
    return {
        "id": new_finding_id("unowned", skill, "default", path),
        "kind": "unowned",
        "severity": "low",
        "skill": skill,
        "expected_owner": None,
        "actual": "default",
        "proposed_fix": (
            "Justify global placement (metadata.hermes.global_justification: "
            "control-plane | shared-primitive | verified-structural-dependency) "
            "or relocate the skill to the owning profile."
        ),
        "discovered_at": utc_now_iso(),
        "status": "open",
    }
