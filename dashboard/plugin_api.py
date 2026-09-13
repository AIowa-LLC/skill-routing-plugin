"""skill-owner-routing — dashboard backend API (SPEC-2 §REST/WS contract v1.0, FROZEN).

Mounted by the Hermes web server at /api/plugins/skill-owner-routing/ when the
plugin is in plugins.enabled (manifest.json declares api=plugin_api.py; the
loader imports this file standalone and mounts its `router`).

Endpoints (paths relative to the mount prefix):
    GET  /map                     → V1 rows[] + meta{profiles[], last_audit_ts}
    GET  /map/{skill_id}          → detail drawer: frontmatter, rationale, history[]
    GET  /drift                   → V2 findings[] + meta{open_count, counts_by_severity}
    GET  /drift/summary           → V4 chip {open_count, worst_severity}
    POST /audit/run               → 202 {run_id}
    GET  /audit/runs/{run_id}     → {state: running|done|failed, findings_count}
    GET  /policy                  → {enabled, require_owner_metadata, route_from_default,
                                    key_present, core_enforces, posture}
    PUT  /policy                  → gated write → resulting policy + config diff
    POST /drift/{id}/resolve      → confirm-gated; returns the resolved finding
    WS   /events                  → push "invalidate" pings (poll fallback remains the floor)

Findings serialize the canonical SPEC-0 Interface 3 record:
    {id, kind, severity, skill, expected_owner, actual, proposed_fix,
     discovered_at, status}  (+ resolved_at on resolved records only)
    kind ∈ {drifted, misplaced-global, unknown-owner, duplicate/hoarding, unowned}
    severity ∈ {info, warning, critical}

Engine integration (BUILD-1, skill_owner_routing package — same repo):
The engine is authoritative when importable. Its real interfaces are:
    policy.read_policy() → {enabled, require_owner_metadata, route_from_default}
    policy has NO writer — PUT /policy performs the gated config write itself.
    drift.scan() → {run_id, scanned, findings[], counts{total, open}}
    ledger.load_findings() / ledger.update_status(id, status)
    coexistence.create_gate_dormant() → bool (core feature-detect probe)
Engine divergences normalized at this boundary (SPEC-0 §3 makes the canonical
shape BINDING on consumers):
    severity low/medium/high → info/warning/critical
    discovered_at ISO-8601 → epoch ms (the UI's fmtTime expects numeric)
    scope name "default" → V1 location.scope "global"
When the package is absent the manifest's promise holds: the dashboard runs a
built-in scan-fallback with the same canonical output shape.

Constraints (SPEC-1 non-negotiables): no core edits, no secrets, no network
beyond the localhost server itself, atomic writes, DEFAULT-profile config is
the single policy truth (key absent = ENABLED — plugin divergence from core,
documented in README).

License: MIT.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

try:  # sibling module when imported standalone by the web server loader
    from .home_override import _engine_call, _home_override
except ImportError:  # pragma: no cover — arbitrary-name file loads (QA harness)
    _here = str(Path(__file__).resolve().parent)
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from home_override import _engine_call, _home_override  # type: ignore

router = APIRouter()

PLUGIN_ID = "skill-owner-routing"
STATE_RELPATH = "skill-owner-routing/state.json"
POLICY_KEYS = ("enabled", "require_owner_metadata", "route_from_default")
SEVERITY_ORDER = ("info", "warning", "critical")
_SEVERITY_MAP = {"low": "info", "medium": "warning", "high": "critical"}
KIND_DUPLICATE = "duplicate/hoarding"
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_JUSTIFICATIONS = {"control-plane", "shared-primitive", "verified-structural-dependency"}
_CORE_PROBE_TTL = 5.0
_RUN_HISTORY_CAP = 50
_EVENT_HISTORY_CAP = 200
_MAX_PROFILES = 64          # engine drift._MAX_PROFILES parity (bounded fleet)
_MAX_SKILLS_PER_HOME = 2000  # engine drift._MAX_SKILLS_PER_HOME parity

_STATE_LOCK = threading.RLock()
_SOCKS_LOCK = threading.Lock()
_SOCKETS: set = set()
_LOOPS: set = set()
_ENGINE_CACHE: List[Any] = [None, False]  # [module, probed]
_CORE_CACHE: List[Any] = [0.0, False]  # [expires_at, value]


# ---------------------------------------------------------------------------
# Home resolution + tiny local helpers (engine/core imports are all guarded)
# ---------------------------------------------------------------------------

def _default_home() -> Path:
    """Fleet DEFAULT HERMES_HOME (mirrors engine common.fleet_default_home)."""
    try:
        from hermes_constants import get_hermes_home

        current = get_hermes_home()
    except Exception:
        env = os.environ.get("HERMES_HOME")
        current = Path(env).expanduser() if env else Path.home() / ".hermes"
    try:
        resolved = current.resolve()
    except OSError:
        resolved = current
    if resolved.parent.name == "profiles":
        return resolved.parent.parent
    return resolved


def _state_path(home: Optional[Path] = None) -> Path:
    return (home or _default_home()) / STATE_RELPATH


def _truthy(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_ms(value: Any) -> int:
    """Epoch ms from epoch s/ms, ISO-8601, or 0."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ms = float(value)
        return int(ms if ms > 1e12 else ms * 1000)
    if isinstance(value, str) and value:
        try:
            return int(float(value))
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def _parse_frontmatter(content: str) -> Dict[str, Any]:
    """YAML frontmatter parse (core agent.skill_utils parity, guarded import)."""
    try:
        from agent.skill_utils import parse_frontmatter as _core_parse

        fm, _body = _core_parse(content)
        return fm if isinstance(fm, dict) else {}
    except Exception:
        pass
    if content.startswith("\ufeff"):
        content = content[1:]
    if not content.startswith("---"):
        return {}
    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return {}
    raw = content[3 : end.start() + 3]
    try:
        parsed = yaml.safe_load(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        out: Dict[str, Any] = {}
        for line in raw.strip().splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                out[key.strip()] = val.strip()
        return out


def _owner_of(fm: Dict[str, Any]) -> Optional[str]:
    meta = fm.get("metadata")
    if not isinstance(meta, dict):
        return None
    hermes = meta.get("hermes")
    if not isinstance(hermes, dict):
        return None
    raw = hermes.get("owner_profile")
    if raw is None:
        return None
    owner = str(raw).strip().lower()
    return owner or None


def _justification_of(fm: Dict[str, Any]) -> Optional[str]:
    """Engine hoarding.global_justification parity: frontmatter key, then body."""
    meta = fm.get("metadata")
    if isinstance(meta, dict):
        hermes = meta.get("hermes")
        if isinstance(hermes, dict):
            raw = hermes.get("global_justification")
            if isinstance(raw, str) and raw.strip().lower() in _JUSTIFICATIONS:
                return raw.strip().lower()
    return None


# ---------------------------------------------------------------------------
# Engine (BUILD-1) loader — real interfaces, scan-fallback without it
# ---------------------------------------------------------------------------

def _load_engine():
    if _ENGINE_CACHE[1]:
        return _ENGINE_CACHE[0]
    _ENGINE_CACHE[1] = True
    try:
        root = Path(__file__).resolve().parents[1]
        if (root / "skill_owner_routing" / "__init__.py").exists():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
        # Package __init__ is intentionally empty — bind the real submodules.
        policy = importlib.import_module("skill_owner_routing.policy")
        drift = importlib.import_module("skill_owner_routing.drift")
        ledger = importlib.import_module("skill_owner_routing.ledger")
        if (
            callable(getattr(policy, "read_policy", None))
            and callable(getattr(drift, "scan", None))
            and callable(getattr(ledger, "load_findings", None))
        ):
            _ENGINE_CACHE[0] = {
                "policy": policy,
                "drift": drift,
                "ledger": ledger,
                "coexistence": importlib.import_module("skill_owner_routing.coexistence"),
            }
    except Exception:
        _ENGINE_CACHE[0] = None
    return _ENGINE_CACHE[0]


def _canonical_finding(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one engine/fallback finding to the SPEC-0 Interface 3 record."""
    severity = str(raw.get("severity", "info")).strip().lower()
    if severity in _SEVERITY_MAP:
        severity = _SEVERITY_MAP[severity]
    if severity not in SEVERITY_ORDER:
        severity = "info"
    out = {
        "id": str(raw.get("id", "")),
        "kind": str(raw.get("kind", "drifted")),
        "severity": severity,
        "skill": str(raw.get("skill", "")),
        "expected_owner": raw.get("expected_owner"),
        "actual": raw.get("actual"),
        "proposed_fix": str(raw.get("proposed_fix", "")),
        "discovered_at": _to_ms(raw.get("discovered_at")),
        "status": str(raw.get("status", "open")),
    }
    if raw.get("resolved_at"):
        out["resolved_at"] = _to_ms(raw.get("resolved_at"))
    return out


def _engine_findings(home: Path) -> Optional[List[Dict[str, Any]]]:
    engine = _load_engine()
    if engine is None:
        return None
    raw = _engine_call(engine, "load_findings", home, submodule="ledger")
    if raw is None:
        return None
    return [_canonical_finding(f) for f in raw]


# ---------------------------------------------------------------------------
# Policy — DEFAULT profile config is the single truth (SPEC-0 Interface 1)
# ---------------------------------------------------------------------------

def _key_present(home: Path) -> bool:
    try:
        cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    skills = cfg.get("skills") if isinstance(cfg, dict) else None
    return isinstance(skills, dict) and "owner_routing" in skills


def _read_policy_home(home: Path) -> Dict[str, Any]:
    """Plugin posture: key absent = ENABLED (documented divergence from core)."""
    engine = _load_engine()
    raw = _engine_call(engine, "read_policy", home, submodule="policy") if engine is not None else None
    if isinstance(raw, dict):
        return {
            "enabled": _truthy(raw.get("enabled"), True),
            "require_owner_metadata": _truthy(raw.get("require_owner_metadata"), True),
            "route_from_default": _truthy(raw.get("route_from_default"), True),
            "key_present": _key_present(home),
        }
    raw_sub: Any = None
    try:
        cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
        raw_sub = (cfg.get("skills") or {}).get("owner_routing") if isinstance(cfg, dict) else None
    except Exception:
        raw_sub = None
    if isinstance(raw_sub, bool):
        return {"enabled": raw_sub, "require_owner_metadata": True, "route_from_default": True, "key_present": True}
    if not isinstance(raw_sub, dict):
        return {"enabled": True, "require_owner_metadata": True, "route_from_default": True, "key_present": False}
    return {
        "enabled": _truthy(raw_sub.get("enabled"), True),
        "require_owner_metadata": _truthy(raw_sub.get("require_owner_metadata"), True),
        "route_from_default": _truthy(raw_sub.get("route_from_default"), True),
        "key_present": True,
    }


def _write_policy_home(home: Path, patch: Dict[str, bool]) -> Dict[str, Any]:
    """Gated write: only the skills.owner_routing subtree, bools only, atomic.

    Prefers core load_config/save_config under a home override (managed-scope
    + cache semantics); falls back to a local atomic YAML round-trip.
    """
    cfg_path = home / "config.yaml"
    before: Optional[Dict[str, Any]] = None
    cfg: Dict[str, Any] = {}
    try:
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cfg = loaded
    except Exception:
        cfg = {}
    skills = cfg.get("skills")
    if not isinstance(skills, dict):
        skills = {}
        cfg["skills"] = skills
    sub = skills.get("owner_routing")
    if isinstance(sub, dict):
        before = dict(sub)
    merged = {
        "enabled": bool(patch.get("enabled", _truthy((before or {}).get("enabled"), True))),
        "require_owner_metadata": bool(
            patch.get("require_owner_metadata", _truthy((before or {}).get("require_owner_metadata"), True))
        ),
        "route_from_default": bool(
            patch.get("route_from_default", _truthy((before or {}).get("route_from_default"), True))
        ),
    }
    try:
        from hermes_cli.config import load_config, save_config
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        token = set_hermes_home_override(home)
        try:
            core_cfg = load_config() or {}
            core_skills = core_cfg.get("skills")
            if not isinstance(core_skills, dict):
                core_skills = {}
                core_cfg["skills"] = core_skills
            core_skills["owner_routing"] = merged
            save_config(core_cfg)
        finally:
            reset_hermes_home_override(token)
    except Exception:
        skills["owner_routing"] = merged
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg_path.with_name(f".{cfg_path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
        os.replace(tmp, cfg_path)
    result = _read_policy_home(home)
    result["config_diff"] = {"path": str(cfg_path), "before": before, "after": merged}
    return result


def _core_enforces(home: Path) -> bool:
    """SPEC-1 dormancy probe: core symbol present AND core policy enabled."""
    engine = _load_engine()
    if engine is not None:
        value = _engine_call(engine, "create_gate_dormant", home, submodule="coexistence")
        if isinstance(value, bool):
            return value
    now = time.monotonic()
    if now < _CORE_CACHE[0]:
        return _CORE_CACHE[1]
    value = False
    try:
        import tools.skill_manager_tool as smt

        fn = getattr(smt, "_skill_owner_routing_policy", None)
        if fn is not None:
            value = bool((fn() or {}).get("enabled"))
    except Exception:
        value = False
    _CORE_CACHE[0] = now + _CORE_PROBE_TTL
    _CORE_CACHE[1] = value
    return value


def _posture(policy: Dict[str, Any], core_enforces: bool) -> str:
    if core_enforces:
        return "core-managed"
    if policy.get("key_present") and not policy.get("enabled", True):
        return "user-disabled"
    return "default-on"


# ---------------------------------------------------------------------------
# Fleet enumeration + scan (engine-authoritative; fallback when absent)
# ---------------------------------------------------------------------------

def _enumerate(home: Path) -> Dict[str, Any]:
    """All skill copies: name → [{scope(global|profile), profile, path, owner, fm}].

    Engine-authoritative: when the engine is importable, the inventory IS the
    engine's own discovery output (drift._all_homes + drift._skills_in — the
    same functions the scanner uses), so /map can never disagree with the
    engine about what exists. Otherwise the local port mirrors those
    semantics (see _enumerate_local).
    """
    engine = _load_engine()
    if engine is not None:
        derived = _engine_inventory(engine, home)
        if derived is not None:
            return derived
    return _enumerate_local(home)


def _engine_inventory(engine: Dict[str, Any], home: Path) -> Optional[Dict[str, Any]]:
    """Fleet inventory derived from the engine's discovery (single truth).

    Runs the engine's own home selection (profiles whose skills dir resolves
    onto the global skills dir are the same store and are skipped) and skill
    discovery (top-level + category-nested, cross-home aliases skipped).
    Returns None when the engine's discovery interface is unavailable so the
    caller can fall back to the local port.
    """
    drift_mod = engine.get("drift")
    skills_in = getattr(drift_mod, "_skills_in", None)
    homes_raw = _engine_call(engine, "_all_homes", home, submodule="drift")
    if not isinstance(homes_raw, list) or not callable(skills_in):
        return None
    profiles: List[str] = []
    copies: Dict[str, List[Dict[str, Any]]] = {}
    seen_global = False
    for item in homes_raw:
        try:
            scope, scope_home = item
        except (TypeError, ValueError):
            continue
        if scope == "default" and not seen_global:
            v1_scope, profile = "global", None  # scope "default" → V1 "global"
            seen_global = True
        elif _PROFILE_ID_RE.match(str(scope)):
            v1_scope, profile = "profile", str(scope)
            profiles.append(profile)
        else:
            continue  # dot-profiles etc. are not V1 profiles (no rows, no chips)
        try:
            found: List[Any] = list(skills_in(Path(scope_home)))  # type: ignore[arg-type]
        except Exception:
            return None
        for name, skill_md in found:
            skill_md = Path(skill_md)
            try:
                fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            copies.setdefault(str(name), []).append(
                {
                    "scope": v1_scope,
                    "profile": profile,
                    "path": str(skill_md.parent),
                    "owner": _owner_of(fm),
                    "fm": fm,
                }
            )
    return {"profiles": sorted(profiles), "copies": copies}


def _resolves_within(path: Path, root: Path) -> bool:
    """Port of engine drift._resolves_within (alias detection)."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _skills_in_scope(skills_dir: Path, scope_root: Path) -> List[Path]:
    """SKILL.md paths at one or two levels — port of engine drift._skills_in.

    Category-nested skills (skills/<category>/<name>/SKILL.md) are visible;
    skill dirs that are symlinks resolving outside the scanned home are
    aliases counted in their real home, not here.
    """
    found: List[Path] = []
    try:
        children = sorted(skills_dir.iterdir())
    except OSError:
        return found
    for child in children:
        if len(found) >= _MAX_SKILLS_PER_HOME:
            break
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.is_symlink() and not _resolves_within(child, scope_root):
            continue  # alias to another home's skill — counted there, not here
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            found.append(skill_md)
            continue
        try:
            grandchildren = sorted(child.iterdir())
        except OSError:
            continue
        for grandchild in grandchildren:
            if len(found) >= _MAX_SKILLS_PER_HOME:
                break
            if not grandchild.is_dir():
                continue
            if grandchild.is_symlink() and not _resolves_within(grandchild, scope_root):
                continue  # alias to another home's skill
            nested = grandchild / "SKILL.md"
            if nested.is_file():
                found.append(nested)
    return found


def _enumerate_local(home: Path) -> Dict[str, Any]:
    """Fallback enumeration — port of the engine's discovery semantics.

    Profiles whose ``skills`` directory resolves INTO the global skills
    directory are skipped: the default profile conventionally symlinks
    ``profiles/default/skills -> ../../skills`` (global skills ARE default's
    skills). Enumerating both would render every global skill twice (once as
    a phantom profile:: row) and mint duplicate/hoarding findings for a
    single physical file.
    """
    try:
        global_skills_real = (home / "skills").resolve()
    except OSError:
        global_skills_real = None
    profiles: List[str] = []
    try:
        children = sorted((home / "profiles").iterdir())
    except OSError:
        children = []
    for child in children[:_MAX_PROFILES]:
        if not child.is_dir() or not _PROFILE_ID_RE.match(child.name):
            continue
        if global_skills_real is not None:
            try:
                child_skills_real = (child / "skills").resolve()
            except OSError:
                child_skills_real = None
            if child_skills_real == global_skills_real:
                continue  # symlinked onto the global skills dir — same store
        profiles.append(child.name)
    copies: Dict[str, List[Dict[str, Any]]] = {}
    for scope, profile in [("global", None)] + [("profile", p) for p in profiles]:
        skills_dir = home / "skills" if scope == "global" else home / "profiles" / (profile or "") / "skills"
        for skill_md in _skills_in_scope(skills_dir, skills_dir.parent):
            try:
                fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            copies.setdefault(skill_md.parent.name, []).append(
                {
                    "scope": scope,
                    "profile": profile,
                    "path": str(skill_md.parent),
                    "owner": _owner_of(fm),
                    "fm": fm,
                }
            )
    return {"profiles": profiles, "copies": copies}


def _rows_from(copies: Dict[str, List[Dict[str, Any]]], profiles: List[str]) -> Dict[str, Any]:
    """V1 rows + fallback findings derived from the enumerated fleet."""
    registered = {"default", *profiles}
    dup_names = {n for n, c in copies.items() if len({x["scope"] for x in c}) > 1}
    rows: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    for name in sorted(copies):
        for copy in copies[name]:
            owner, scope, profile = copy["owner"], copy["scope"], copy["profile"]
            if name in dup_names:
                state = "duplicate"
            elif owner and owner not in registered:
                state = "unknown-owner"
            elif owner and scope == "global":
                state = "drifted"  # misplaced-global renders as drifted in V1
            elif owner and scope == "profile" and owner != profile:
                state = "drifted"
            elif owner is None:
                state = "clean" if scope == "global" and _justification_of(copy["fm"]) else "unowned"
            else:
                state = "clean"
            rows.append(
                {
                    "skill_id": f"{scope}::{name}",
                    "name": name,
                    "category": str(copy["fm"].get("category") or ""),
                    "owner_profile": owner,
                    "location": {"scope": scope, "profile": profile, "path": copy["path"]},
                    "state": state,
                }
            )
            if state == "duplicate" and scope == "global":
                owners = [c["owner"] or c["profile"] for c in copies[name] if c["scope"] == "profile"]
                findings.append(_finding(KIND_DUPLICATE, "warning", name, owners[0] if owners else None,
                                         "default+" + "+".join(sorted(c["profile"] or "default" for c in copies[name])),
                                         "Keep the profile-owned copy; delete the global duplicate "
                                         "(No-Specialist-Hoarding).",
                                         path=copy["path"]))
            elif state == "unknown-owner":
                findings.append(_finding("unknown-owner", "warning", name, owner, scope,
                                         f"owner_profile {owner!r} is not a registered profile; "
                                         "re-assign the owner or remove the stale metadata.",
                                         path=copy["path"]))
            elif owner and scope == "global":
                findings.append(_finding("misplaced-global", "warning", name, owner, "default",
                                         f"Move the skill to the {owner} profile home, or strip the "
                                         "owner metadata with a global justification.",
                                         path=copy["path"]))
            elif owner and scope == "profile" and owner != profile:
                findings.append(_finding("drifted", "critical", name, owner, profile or scope,
                                         f"Move to {owner}, archive the {profile} copy.",
                                         path=copy["path"]))
            elif state == "unowned":
                findings.append(_finding("unowned", "info", name, None, "default",
                                         "Declare metadata.hermes.owner_profile, or justify global "
                                         "placement via metadata.hermes.global_justification.",
                                         path=copy["path"]))
    return {"profiles": profiles, "rows": rows, "findings": sorted(findings, key=lambda f: (f["skill"], f["kind"]))}


def _finding(kind: str, severity: str, skill: str, expected: Optional[str], actual: str, fix: str,
             path: str = "") -> Dict[str, Any]:
    """Fallback finding — id digest mirrors engine common.new_finding_id
    (kind|scope|skill|resolved-path) so two physical copies of one skill
    name get distinct ids while symlink aliases of a single file share one."""
    resolved = ""
    if path:
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = str(path)
    sig = hashlib.sha256(f"{kind}|{actual}|{skill}|{resolved}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"{kind}-{sig}",
        "kind": kind,
        "severity": severity,
        "skill": skill,
        "expected_owner": expected,
        "actual": actual,
        "proposed_fix": fix,
        "discovered_at": 0,  # stamped per run
        "status": "open",
    }


def _scan_fleet(home: Path) -> Dict[str, Any]:
    """Engine-authoritative scan (findings + ledger upsert); fallback enumeration."""
    engine = _load_engine()
    if engine is not None:
        result = _engine_call(engine, "scan", home, submodule="drift")
        if isinstance(result, dict) and isinstance(result.get("findings"), list):
            findings = [_canonical_finding(f) for f in result["findings"]]
            enum = _enumerate(home)
            built = _rows_from(enum["copies"], enum["profiles"])
            kinds_by_skill: Dict[str, set] = {}
            for finding in findings:
                kinds_by_skill.setdefault(finding["skill"], set()).add(finding["kind"])
            for row in built["rows"]:
                kinds = kinds_by_skill.get(row["name"], set())
                if KIND_DUPLICATE in kinds:
                    row["state"] = "duplicate"
                elif "unknown-owner" in kinds:
                    row["state"] = "unknown-owner"
                elif {"drifted", "misplaced-global"} & kinds:
                    row["state"] = "drifted"
                elif "unowned" in kinds and not row["owner_profile"]:
                    row["state"] = "unowned"
            return {"profiles": enum["profiles"], "rows": built["rows"], "findings": findings}
    enum = _enumerate(home)
    return _rows_from(enum["copies"], enum["profiles"])


# ---------------------------------------------------------------------------
# Ledger state (DEFAULT home — the fleet-level surface)
# ---------------------------------------------------------------------------

def _load_state(home: Optional[Path] = None) -> Dict[str, Any]:
    path = _state_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"schema": 1, "last_audit_ts": None, "last_scan_ts": None, "runs": {},
            "rows": [], "profiles": [], "history": []}


def _save_state(state: Dict[str, Any], home: Optional[Path] = None) -> None:
    _atomic_write_json(_state_path(home), state)


def _append_history(state: Dict[str, Any], event: str, skill: Optional[str]) -> None:
    state.setdefault("history", []).append({"skill": skill, "event": event, "at": _now_ms()})
    del state["history"][:-_EVENT_HISTORY_CAP]


def _ensure_scan(state: Dict[str, Any], home: Path) -> None:
    if state.get("rows"):
        # Engine findings land in its ledger only via a scan; run one so the
        # Drift Feed shows fixture drift before any explicit audit.
        engine = _load_engine()
        if engine is not None and _engine_findings(home) is None:
            _engine_call(engine, "scan", home, submodule="drift")
        elif engine is None and "findings" not in state:
            # M7 backfill: rows present but the fallback feed was never
            # seeded (pre-fix state.json) — populate it via our own scan.
            _merge_fallback_findings(state, _scan_fleet(home)["findings"])
        return
    _merge_scan(state, _scan_fleet(home), audit=False)


def _merge_scan(state: Dict[str, Any], scan: Dict[str, Any], audit: bool) -> int:
    stamp = _now_ms()
    for finding in scan["findings"]:
        if not finding.get("discovered_at"):
            finding["discovered_at"] = stamp
    state["rows"] = scan["rows"]
    state["profiles"] = scan["profiles"]
    state["last_scan_ts"] = stamp
    if audit:
        state["last_audit_ts"] = stamp
    if "findings" in scan and _load_engine() is None:
        # Fallback mode owns its findings in state.json; when the engine is
        # importable its own ledger stays the single findings surface.
        return _merge_fallback_findings(state, scan["findings"])
    return sum(1 for f in scan["findings"] if f.get("status") == "open")


def _merge_fallback_findings(state: Dict[str, Any], findings: List[Dict[str, Any]]) -> int:
    """Fallback findings upsert — engine ledger.upsert_findings parity.

    Deterministic ids keep resolved/acknowledged status across rescans;
    drift that no longer appears is dropped (the drift is gone). Returns
    the open count, mirroring the engine ledger's counts.open."""
    stamp = _now_ms()
    prior = {f["id"]: f for f in state.get("findings", []) if isinstance(f, dict) and f.get("id")}
    merged: Dict[str, Any] = {}
    for finding in findings:
        if not finding.get("discovered_at"):
            finding["discovered_at"] = stamp
        kept = prior.get(finding["id"])
        if kept is not None and kept.get("status") in {"resolved", "acknowledged"}:
            revived = {**finding, "status": kept["status"]}
            if kept.get("resolved_at"):
                revived["resolved_at"] = kept["resolved_at"]
            merged[finding["id"]] = revived
        else:
            merged[finding["id"]] = finding
    state["findings"] = sorted(merged.values(), key=lambda f: (f.get("kind", ""), f.get("skill", "")))
    return sum(1 for f in state["findings"] if f.get("status") == "open")


def _live_findings(state: Dict[str, Any], home: Path) -> List[Dict[str, Any]]:
    """Engine ledger is authoritative (external watchdog scans surface too)."""
    findings = _engine_findings(home)
    if findings is not None:
        return findings
    return state.get("findings", [])


def _ensure_fallback_feed(state: Dict[str, Any], home: Path) -> bool:
    """Fallback-mode Drift Feed seeding: never serve an empty feed while the
    dashboard's own enumeration sees drift. Covers cold start (no /map yet)
    and pre-fix state.json (rows present, findings key never written).
    Returns True when state was mutated (caller persists under its lock)."""
    if _load_engine() is not None:
        return False  # engine ledger is the feed — nothing to seed
    if "findings" in state:
        return False  # already seeded (empty list = clean fleet, not absent)
    scan = _scan_fleet(home)
    state.setdefault("rows", scan["rows"])
    state.setdefault("profiles", scan["profiles"])
    _merge_fallback_findings(state, scan["findings"])
    return True


def _last_audit_ts(state: Dict[str, Any], findings: List[Dict[str, Any]]) -> Optional[int]:
    stamps = [f.get("discovered_at") or 0 for f in findings]
    ledger_max = max(stamps) if stamps else 0
    stored = state.get("last_audit_ts") or 0
    best = max(ledger_max, stored)
    return best or None


def _run_audit(home: Path, run_id: str) -> None:
    try:
        scan = _scan_fleet(home)
        count = sum(1 for f in _live_findings({}, home) if f.get("status") == "open") or sum(
            1 for f in scan["findings"] if f.get("status") == "open"
        )
        record = {
            "state": "done", "started_at": _now_ms(),
            "finished_at": _now_ms(), "findings_count": count,
        }
        _append_audit_history = ("audit: open findings refreshed", None)
    except Exception as exc:  # noqa: BLE001 — surfaced via the run registry
        scan = {}
        record = {"state": "failed", "finished_at": _now_ms(), "error": str(exc)[:200]}
        _append_audit_history = None
    # Re-read + merge under the lock: concurrent runs must not clobber each
    # other's registry entries (state.json is shared, not per-run).
    with _STATE_LOCK:
        state = _load_state(home)
        started = state.get("runs", {}).get(run_id, {}).get("started_at")
        if started:
            record["started_at"] = started
        state.setdefault("runs", {})[run_id] = record
        if _append_audit_history is not None:
            event, skill = _append_audit_history
            _append_history(state, event, skill)
        runs = state["runs"]
        if len(runs) > _RUN_HISTORY_CAP:
            keep = sorted(runs.items(), key=lambda kv: kv[1].get("finished_at", 0), reverse=True)[:_RUN_HISTORY_CAP]
            state["runs"] = dict(keep)
        _merge_rows(state, scan if _append_audit_history is not None else {"rows": [], "profiles": []})
        if _append_audit_history is not None and _load_engine() is None:
            # M7: fallback audits refresh the Drift Feed; findings_count
            # reports the MERGED feed (resolutions preserved), mirroring
            # engine mode where the ledger's post-upsert open count is it.
            record["findings_count"] = _merge_fallback_findings(state, scan.get("findings", []))
        _save_state(state, home)
    _broadcast("invalidate")


def _merge_rows(state: Dict[str, Any], scan: Dict[str, Any]) -> None:
    if scan.get("rows"):
        state["rows"] = scan["rows"]
        state["profiles"] = scan.get("profiles", state.get("profiles", []))
    stamp = _now_ms()
    state["last_scan_ts"] = stamp
    if state.get("runs") and all(r.get("state") == "done" for r in state["runs"].values() if r):
        state["last_audit_ts"] = stamp


# ---------------------------------------------------------------------------
# WS push (best-effort; UI keeps the poll fallback as the floor)
# ---------------------------------------------------------------------------

def _broadcast(message: str) -> None:
    with _SOCKS_LOCK:
        loops = list(_LOOPS)
        socks = list(_SOCKETS)
    for loop in loops:
        for sock in socks:
            try:
                asyncio.run_coroutine_threadsafe(sock.send_text(message), loop).result(timeout=1.0)
            except Exception:
                pass


@router.websocket("/events")
async def events(ws: WebSocket) -> None:
    await ws.accept()
    loop = asyncio.get_running_loop()
    with _SOCKS_LOCK:
        _SOCKETS.add(ws)
        _LOOPS.add(loop)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        with _SOCKS_LOCK:
            _SOCKETS.discard(ws)
            if not _SOCKETS:
                _LOOPS.discard(loop)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/map")
def get_map() -> Dict[str, Any]:
    home = _default_home()
    with _STATE_LOCK:
        state = _load_state(home)
        _ensure_scan(state, home)
        rows = list(state.get("rows", []))
        last_audit = _last_audit_ts(state, _live_findings(state, home))
        _save_state(state, home)
    for row in rows:
        row["last_audit"] = last_audit
    return {"rows": rows, "meta": {"profiles": state.get("profiles", []), "last_audit_ts": last_audit}}


@router.get("/map/{skill_id}")
def get_map_detail(skill_id: str) -> Dict[str, Any]:
    home = _default_home()
    with _STATE_LOCK:
        state = _load_state(home)
        _ensure_scan(state, home)
        rows = state.get("rows", [])
        history_source = state.get("history", [])
        _save_state(state, home)
    row = next((r for r in rows if r["skill_id"] == skill_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id!r} is not mapped.")
    skill_md = Path(row["location"]["path"]) / "SKILL.md"
    try:
        fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        fm = {}
    owner, scope = row.get("owner_profile"), row["location"]["scope"]
    if scope == "global":
        justified = _justification_of(fm) is not None
        rationale = {
            "hoarding_justified": justified,
            "text": (
                f"Global scope justified as {_justification_of(fm)}."
                if justified
                else "Global scope without a control-plane / shared-primitive / "
                "verified-structural-dependency justification."
            ),
        }
    elif row["state"] == "drifted":
        rationale = {
            "hoarding_justified": False,
            "text": f"Owner profile {owner!r}; actual location {scope} "
            f"{row['location'].get('profile') or ''}".strip() + ".",
        }
    elif row["state"] == "unowned":
        rationale = {
            "hoarding_justified": False,
            "text": "No owner_profile declared — new skills should declare "
            "metadata.hermes.owner_profile.",
        }
    else:
        rationale = {"hoarding_justified": None, "text": f"Owned by {owner!r}." if owner else "No owner declared."}
    history = [
        {"event": h["event"], "at": h["at"]}
        for h in history_source
        if h.get("skill") == row["name"]
    ]
    return {
        "skill_id": skill_id,
        "name": row["name"],
        "owner_profile": owner,
        "location": row["location"],
        "state": row["state"],
        "frontmatter": fm,
        "rationale": rationale,
        "history": history,
    }


def _drift_meta(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    open_findings = [f for f in findings if f.get("status") == "open"]
    counts = {sev: sum(1 for f in open_findings if f["severity"] == sev) for sev in SEVERITY_ORDER}
    worst = next((sev for sev in reversed(SEVERITY_ORDER) if counts[sev]), None)
    return {"open_count": len(open_findings), "counts_by_severity": counts, "worst_severity": worst}


def _ensure_ledger_populated(home: Path) -> None:
    """Cold-start: one scan so the feed reflects current drift, not just past
    audits. The engine ledger is the surface shared with the cron watchdog —
    we only seed it when it has never been written."""
    engine = _load_engine()
    if engine is None:
        return
    ledger_file = home / "skills" / ".skill_owner_findings.json"
    if not ledger_file.exists():
        _engine_call(engine, "scan", home, submodule="drift")


@router.get("/drift")
def get_drift() -> Dict[str, Any]:
    home = _default_home()
    _ensure_ledger_populated(home)
    with _STATE_LOCK:
        state = _load_state(home)
        if _ensure_fallback_feed(state, home):
            _save_state(state, home)
    findings = _live_findings(state, home)
    meta = _drift_meta(findings)
    return {"findings": findings, "meta": {k: meta[k] for k in ("open_count", "counts_by_severity")}}


@router.get("/drift/summary")
def get_drift_summary() -> Dict[str, Any]:
    home = _default_home()
    _ensure_ledger_populated(home)
    with _STATE_LOCK:
        state = _load_state(home)
        if _ensure_fallback_feed(state, home):
            _save_state(state, home)
    meta = _drift_meta(_live_findings(state, home))
    return {"open_count": meta["open_count"], "worst_severity": meta["worst_severity"]}


@router.post("/audit/run", status_code=202)
def post_audit_run() -> Dict[str, Any]:
    home = _default_home()
    run_id = uuid.uuid4().hex[:12]
    with _STATE_LOCK:
        state = _load_state(home)
        state.setdefault("runs", {})[run_id] = {"state": "running", "started_at": _now_ms()}
        _save_state(state, home)
    threading.Thread(target=_run_audit, args=(home, run_id), daemon=True, name=f"sor-audit-{run_id}").start()
    return {"run_id": run_id}


@router.get("/audit/runs/{run_id}")
def get_audit_run(run_id: str) -> Dict[str, Any]:
    with _STATE_LOCK:
        state = _load_state(_default_home())
    run = state.get("runs", {}).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Audit run {run_id!r} not found.")
    return {"state": run.get("state"), "findings_count": run.get("findings_count")}


@router.get("/policy")
def get_policy() -> Dict[str, Any]:
    home = _default_home()
    policy = _read_policy_home(home)
    core = _core_enforces(_default_home())
    return {
        "enabled": policy["enabled"],
        "require_owner_metadata": policy["require_owner_metadata"],
        "route_from_default": policy["route_from_default"],
        "key_present": policy["key_present"],
        "core_enforces": core,
        "posture": _posture(policy, core),
    }


class PolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    require_owner_metadata: Optional[bool] = None
    route_from_default: Optional[bool] = None
    confirm: Optional[bool] = None


@router.put("/policy")
def put_policy(patch: PolicyPatch) -> Dict[str, Any]:
    if patch.confirm is False:
        raise HTTPException(status_code=400, detail="Policy write not confirmed.")
    updates = {k: getattr(patch, k) for k in POLICY_KEYS if getattr(patch, k) is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No policy fields to update.")
    home = _default_home()
    result = _write_policy_home(home, updates)
    diff = result.pop("config_diff", None)
    core = _core_enforces(_default_home())
    policy_view = {
        "enabled": result["enabled"],
        "require_owner_metadata": result["require_owner_metadata"],
        "route_from_default": result["route_from_default"],
        "key_present": result["key_present"],
        "core_enforces": core,
        "posture": _posture(result, core),
    }
    with _STATE_LOCK:
        state = _load_state(home)
        _append_history(state, f"policy: {json.dumps(updates, sort_keys=True)}", None)
        _save_state(state, home)
    _broadcast("invalidate")
    return {"policy": policy_view, "config_diff": diff}


@router.post("/drift/{finding_id}/resolve")
def post_resolve(finding_id: str) -> Dict[str, Any]:
    home = _default_home()
    engine = _load_engine()
    with _STATE_LOCK:
        state = _load_state(home)
        source = _live_findings(state, home)
        finding = next((f for f in source if f.get("id") == finding_id), None)
        if finding is None:
            raise HTTPException(status_code=404, detail=f"Drift finding {finding_id!r} not found.")
        if finding.get("status") == "resolved":
            raise HTTPException(status_code=409, detail="Finding is already resolved.")
        if engine is not None:
            try:
                with _home_override(home):
                    updated = engine["ledger"].update_status(finding_id, "resolved")
            except Exception as exc:
                # The ledger write failed — report it, never fake a success.
                # Only the exception CLASS is surfaced, never internals.
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Resolve failed: drift ledger write did not complete "
                        f"({type(exc).__name__}). The finding remains open; "
                        f"no resolution was recorded."
                    ),
                )
            if isinstance(updated, dict):
                finding = _canonical_finding(updated)
                if finding["status"] == "resolved" and not finding.get("resolved_at"):
                    finding["resolved_at"] = _now_ms()
            else:
                # Ledger no longer knows this id (e.g. re-scan dropped it):
                # nothing was written — treat as not-found rather than
                # recording a false resolution.
                raise HTTPException(
                    status_code=404,
                    detail=f"Drift finding {finding_id!r} not found.",
                )
        else:
            finding["status"] = "resolved"
            finding["resolved_at"] = _now_ms()
            state_findings = state.setdefault("findings", source)
            for i, existing in enumerate(state_findings):
                if existing.get("id") == finding_id:
                    state_findings[i] = finding
                    break
        _append_history(state, f"resolved: {finding['kind']} ({finding['skill']})", finding["skill"])
        _save_state(state, home)
    _broadcast("invalidate")
    return {"finding": finding}
