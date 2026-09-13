"""REBUILT ownership-drift watchdog — no upstream basis (SPEC-1 §3).

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Scans all profiles + global (default home): owner_profile vs actual
location, unknown owner ids, global+profile duplicates (hoarding signal),
unjustified global scope. Emits findings in the canonical SPEC-0 Interface 3
record shape. PROPOSES fixes, never auto-mutates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import fleet_default_home, new_finding_id, utc_now_iso
from .frontmatter import declared_skill_owner
from .hoarding import lint_global_skill, global_justification
from . import ledger as ledger_mod

logger = logging.getLogger(__name__)

_MAX_PROFILES = 64
_MAX_SKILLS_PER_HOME = 2000


def scan() -> Dict[str, Any]:
    """Full fleet scan. Returns {"run_id", "scanned", "findings", "counts"}."""
    run_id = f"drift-{utc_now_iso()}"
    homes = _all_homes()
    catalog: List[Dict[str, Any]] = []
    scanned = 0
    for scope, home in homes:
        for skill, skill_md in _skills_in(home):
            scanned += 1
            frontmatter, body = _read(skill_md)
            catalog.append(
                {
                    "scope": scope,
                    "skill": skill,
                    "path": str(skill_md),
                    "owner": declared_skill_owner_from(frontmatter),
                    "frontmatter": frontmatter,
                    "body": body,
                }
            )

    findings = _analyze(catalog, [scope for scope, _ in homes])
    counts = ledger_mod.upsert_findings(findings)
    from .index import update_index

    update_index({e["skill"]: e["path"] for e in catalog})
    return {
        "run_id": run_id,
        "scanned": scanned,
        "findings": findings,
        "counts": counts,
    }


def declared_skill_owner_from(frontmatter: Dict[str, Any]) -> Optional[str]:
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return None
    hermes = metadata.get("hermes")
    if not isinstance(hermes, dict):
        return None
    raw = hermes.get("owner_profile")
    if raw is None:
        return None
    owner = str(raw).strip().lower()
    return owner or None


# ── discovery ─────────────────────────────────────────────────────────────


def _all_homes() -> List[Tuple[str, Path]]:
    """(scope, home) pairs: default home + every profile dir (bounded).

    Profiles whose ``skills`` directory resolves INTO the default home's
    skills directory are skipped: the default profile conventionally
    symlinks ``profiles/default/skills -> ../../skills`` (global skills ARE
    default's skills). Scanning both would double-count every global skill
    and manufacture phantom duplicate/drift findings for a single physical
    file.
    """
    homes: List[Tuple[str, Path]] = []
    default_home = fleet_default_home()
    homes.append(("default", default_home))
    try:
        default_skills_real = (default_home / "skills").resolve()
    except OSError:
        default_skills_real = None
    profiles_root = default_home / "profiles"
    try:
        children = sorted(profiles_root.iterdir())
    except OSError:
        children = []
    for child in children[:_MAX_PROFILES]:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue  # vanished/unstatable between listing and stat
        if default_skills_real is not None:
            try:
                child_skills_real = (child / "skills").resolve()
            except OSError:
                child_skills_real = None
            if child_skills_real == default_skills_real:
                continue  # symlinked onto the global/default skills dir
        homes.append((child.name, child))
    return homes


def _skills_in(home: Path) -> List[Tuple[str, Path]]:
    """Top-level + one-category-nested skills, bounded per home.

    Permission/OSError on any single child (unreadable dir, broken stat) is
    skipped, never propagated — one hostile directory must not kill the
    whole watchdog scan (same contract as run_audit).
    """
    skills_dir = home / "skills"
    found: List[Tuple[str, Path]] = []
    try:
        children = sorted(skills_dir.iterdir())
    except OSError:
        return found
    for child in children:
        if len(found) >= _MAX_SKILLS_PER_HOME:
            break
        try:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.is_symlink() and not _resolves_within(child, home):
                continue  # alias to another home's skill — counted there, not here
            skill_md = child / "SKILL.md"
            if skill_md.is_file():
                if _contained_skill_md(home, skill_md):
                    found.append((child.name, skill_md))
                continue
            grandchildren = sorted(child.iterdir())
        except OSError:
            continue  # unreadable/undiscoverable — skip this child only
        for grandchild in grandchildren:
            if len(found) >= _MAX_SKILLS_PER_HOME:
                break
            try:
                if not grandchild.is_dir():
                    continue
                if grandchild.is_symlink() and not _resolves_within(grandchild, home):
                    continue  # alias to another home's skill
                nested = grandchild / "SKILL.md"
                if nested.is_file():
                    if _contained_skill_md(home, nested):
                        found.append((grandchild.name, nested))
            except OSError:
                continue  # unreadable/undiscoverable — skip this grandchild only
    return found


_SKIP_WARN_CAP = 32
_skip_warns: list = []


def _contained_skill_md(home: Path, skill_md: Path) -> bool:
    """M11 (SECURITY-CONTRACT): a SKILL.md reachable only through a symlink
    component, or resolving outside the fleet root / scanned home, is never
    indexed — the discovery read must not escape the fleet root and disclose
    outside content. Uncertainty means skip + bounded warning, never read."""
    try:
        skills_dir = home / "skills"
        target_real = skill_md.resolve()
        if not target_real.is_relative_to(home.resolve()):
            _warn_skip()
            return False
        if skill_md.is_symlink():
            _warn_skip()
            return False
        rel = skill_md.relative_to(skills_dir)
        cur = skills_dir
        for part in rel.parts:
            cur = cur / part
            if cur.is_symlink():
                _warn_skip()
                return False
        return True
    except OSError:
        _warn_skip()
        return False


def _warn_skip() -> None:
    import time as _time

    _skip_warns.append(_time.monotonic())
    del _skip_warns[:-_SKIP_WARN_CAP]
    logger.warning(
        "skill-owner-routing: skill path failed containment validation; skipped"
    )


def _resolves_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _read(skill_md: Path) -> Tuple[Dict[str, Any], str]:
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}, ""
    try:
        from agent.skill_utils import parse_frontmatter

        frontmatter, body = parse_frontmatter(content)
        if not isinstance(frontmatter, dict):
            frontmatter = {}
        return frontmatter, body
    except Exception:
        return {}, content


# ── analysis ──────────────────────────────────────────────────────────────


def _analyze(
    catalog: List[Dict[str, Any]], known_scopes: List[str]
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    scopes = set(known_scopes)
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for entry in catalog:
        by_name.setdefault(entry["skill"], []).append(entry)

    for entry in catalog:
        scope, skill, owner = entry["scope"], entry["skill"], entry["owner"]
        if owner is not None and owner not in scopes:
            findings.append(
                _finding(
                    "unknown-owner",
                    "medium",
                    skill,
                    owner,
                    scope,
                    f"Skill {skill!r} declares owner_profile={owner!r}, which "
                    "is not a registered profile. Re-assign the owner or "
                    "remove the stale metadata.",
                    entry["path"],
                )
            )
        if owner is not None and scope != "default" and owner != scope:
            findings.append(
                _finding(
                    "drifted",
                    "high",
                    skill,
                    owner,
                    scope,
                    f"Skill {skill!r} lives in profile {scope!r} but declares "
                    f"owner_profile={owner!r}. Move it to {owner!r}'s skills "
                    f"directory or update the metadata to {scope!r}.",
                    entry["path"],
                )
            )
        if scope == "default" and owner is not None:
            justified = global_justification(entry["frontmatter"], entry["body"])
            if justified is None:
                findings.append(
                    _finding(
                        "misplaced-global",
                        "medium",
                        skill,
                        owner,
                        "default",
                        f"Global skill {skill!r} carries owner_profile={owner!r}. "
                        "Either strip the owner metadata (with a global "
                        "justification) or move the skill into the owner profile.",
                        entry["path"],
                    )
                )
        if scope == "default" and owner is None:
            hoarding = lint_global_skill(
                skill, entry["frontmatter"], entry["body"], entry["path"]
            )
            if hoarding is not None:
                findings.append(hoarding)

    for skill, entries in by_name.items():
        scopes_with = {e["scope"] for e in entries}
        if "default" in scopes_with and len(scopes_with) > 1:
            other = sorted(scopes_with - {"default"})
            findings.append(
                _finding(
                    "duplicate/hoarding",
                    "medium",
                    skill,
                    entries[0]["owner"],
                    "default+" + "+".join(other),
                    f"Skill {skill!r} exists globally AND in profile(s) "
                    f"{other!r}. Keep the profile-owned copy; delete the "
                    "global duplicate (or vice versa with justification).",
                )
            )
    return findings


def _finding(
    kind: str,
    severity: str,
    skill: str,
    expected_owner: Optional[str],
    actual: str,
    proposed_fix: str,
    path: str = "",
) -> Dict[str, Any]:
    return {
        "id": new_finding_id(kind, skill, actual, path),
        "kind": kind,
        "severity": severity,
        "skill": skill,
        "expected_owner": expected_owner,
        "actual": actual,
        "proposed_fix": proposed_fix,
        "discovered_at": utc_now_iso(),
        "status": "open",
    }


# ── entrypoints ───────────────────────────────────────────────────────────


def run_audit() -> str:
    """On-demand audit for the skill_owner_audit tool. JSON string result."""
    import json

    try:
        result = scan()
        return json.dumps(
            {
                "ok": True,
                "run_id": result["run_id"],
                "scanned": result["scanned"],
                "counts": result["counts"],
                "findings": result["findings"],
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("drift audit failed")
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def watchdog_main() -> int:
    """Cron watchdog entrypoint: exit 0 silent when clean, print digest when
    findings exist (classic no_agent watchdog pattern)."""
    result = scan()
    open_count = result["counts"]["open"]
    if not open_count:
        return 0
    import json

    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "scanned": result["scanned"],
                "open_findings": open_count,
                "summary": _digest(result["findings"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _digest(findings: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for finding in findings[:20]:
        if finding.get("status") != "open":
            continue
        lines.append(
            f"[{finding['severity']}] {finding['kind']}: {finding['skill']} "
            f"(actual={finding['actual']}) — {finding['proposed_fix']}"
        )
    remaining = sum(
        1 for f in findings if f.get("status") == "open"
    ) - len([f for f in findings[:20] if f.get("status") == "open"])
    if remaining > 0:
        lines.append(f"... and {remaining} more open findings")
    return lines
