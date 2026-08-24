"""Name→home skill index for mutation-gate target resolution.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Optional

_INDEX: Dict[str, str] = {}  # skill name -> absolute SKILL.md path
_LOCK = threading.Lock()
_MAX_SCAN_PROFILES = 64  # bounded fallback scan: profile count is small


def update_index(entries: Dict[str, str]) -> None:
    """Replace the index from a drift scan (name -> SKILL.md abs path)."""
    with _LOCK:
        _INDEX.clear()
        _INDEX.update(entries)


def merge_entries(entries: Dict[str, str]) -> None:
    with _LOCK:
        _INDEX.update(entries)


def lookup(name: str) -> Optional[Path]:
    """Resolve a skill name to its SKILL.md, validating by Path.exists().

    Index hit validated with a single stat AND confirmed to sit inside the
    current fleet root (a process can switch HERMES_HOME between profiles);
    on miss (or stale hit) falls back to a bounded scan across profile
    homes. Returns None when unresolvable.
    """
    with _LOCK:
        indexed = _INDEX.get(name)
    if indexed is not None:
        candidate = Path(indexed)
        if candidate.exists() and _within_current_fleet(candidate):
            return candidate

    found = _bounded_scan(name)
    if found is not None:
        with _LOCK:
            _INDEX[name] = str(found)
    else:
        with _LOCK:
            _INDEX.pop(name, None)  # drop stale entries on confirmed miss
    return found


def _within_current_fleet(candidate: Path) -> bool:
    """True when the path sits under the fleet root the index was built for."""
    try:
        from .common import fleet_default_home

        root = fleet_default_home().resolve()
        return candidate.resolve().is_relative_to(root)
    except Exception:
        return False


def clear() -> None:
    with _LOCK:
        _INDEX.clear()


def _profile_roots() -> list:
    """All candidate skills roots: current home's + DEFAULT root's profiles."""
    roots = []
    seen = set()

    def add_home(home: Path) -> None:
        skills_dir = home / "skills"
        if skills_dir not in seen:
            seen.add(skills_dir)
            roots.append(skills_dir)

    try:
        from hermes_constants import get_hermes_home

        add_home(Path(get_hermes_home()))
    except Exception:
        pass
    try:
        from .common import fleet_default_home

        default_home = fleet_default_home()
        add_home(default_home)
        profiles_root = default_home / "profiles"
        if profiles_root.is_dir():
            for child in sorted(profiles_root.iterdir())[:_MAX_SCAN_PROFILES]:
                if child.is_dir():
                    add_home(child)
    except Exception:
        pass
    return roots


def _bounded_scan(name: str) -> Optional[Path]:
    """Scan profile skill roots for ``<skills>/<name>/SKILL.md`` (top level
    + one category level). Bounded: 2 rglob levels max, no full descent."""
    for skills_dir in _profile_roots():
        if not skills_dir.is_dir():
            continue
        direct = skills_dir / name / "SKILL.md"
        if direct.is_file():
            return direct
        try:
            children = sorted(skills_dir.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                nested = child / name / "SKILL.md"
                if nested.is_file():
                    return nested
    return None


def build_index_from_scan() -> Dict[str, str]:
    """Full index build — used by drift scan and gate warm-up."""
    entries: Dict[str, str] = {}
    for skills_dir in _profile_roots():
        if not skills_dir.is_dir():
            continue
        try:
            children = sorted(skills_dir.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if skill_md.is_file():
                entries.setdefault(child.name, str(skill_md))
                continue
            try:
                grandchildren = sorted(child.iterdir())
            except OSError:
                continue
            for grandchild in grandchildren:
                if grandchild.is_dir():
                    nested = grandchild / "SKILL.md"
                    if nested.is_file():
                        entries.setdefault(grandchild.name, str(nested))
    return entries
