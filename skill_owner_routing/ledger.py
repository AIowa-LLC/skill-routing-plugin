"""Findings ledger — plugin-owned drift findings store in DEFAULT home.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Canonical finding record (SPEC-0 Interface 3 — BINDING):
{id, kind, severity, skill, expected_owner, actual, proposed_fix,
 discovered_at, status}
kind ∈ {drifted, misplaced-global, unknown-owner, duplicate/hoarding, unowned}
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

KINDS = {
    "drifted",
    "misplaced-global",
    "unknown-owner",
    "duplicate/hoarding",
    "unowned",
}


def ledger_path() -> Path:
    from .common import fleet_default_home

    return fleet_default_home() / "skills" / ".skill_owner_findings.json"


def load_findings() -> List[Dict[str, Any]]:
    path = ledger_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    findings = data.get("findings")
    if not isinstance(findings, list):
        return []
    return [f for f in findings if _valid(f)]


def save_findings(findings: List[Dict[str, Any]]) -> None:
    """Atomic replace (temp + rename) — never a partial write."""
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"findings": findings}, ensure_ascii=False, indent=1, sort_keys=True
    )
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_findings_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def upsert_findings(new_findings: List[Dict[str, Any]]) -> Dict[str, int]:
    """Merge a scan's findings: deterministic ids make re-scans idempotent.

    Existing status ('resolved', 'acknowledged') is preserved for findings
    that reappear; new ones enter as 'open'. Findings no longer present are
    dropped from the ledger (the drift is gone).
    """
    existing = {f["id"]: f for f in load_findings()}
    merged: Dict[str, Any] = {}
    for finding in new_findings:
        prior = existing.get(finding["id"])
        if prior is not None and prior.get("status") in {
            "resolved",
            "acknowledged",
        }:
            merged[finding["id"]] = {**finding, "status": prior["status"]}
        else:
            merged[finding["id"]] = finding
    ordered = sorted(merged.values(), key=lambda f: (f.get("kind", ""), f.get("skill", "")))
    save_findings(ordered)
    return {"total": len(ordered), "open": sum(1 for f in ordered if f.get("status") == "open")}


def update_status(finding_id: str, status: str) -> Optional[Dict[str, Any]]:
    findings = load_findings()
    for finding in findings:
        if finding["id"] == finding_id:
            finding["status"] = status
            save_findings(sorted(findings, key=lambda f: (f.get("kind", ""), f.get("skill", ""))))
            return finding
    return None


def _valid(finding: Any) -> bool:
    if not isinstance(finding, dict):
        return False
    return (
        isinstance(finding.get("id"), str)
        and finding.get("kind") in KINDS
        and isinstance(finding.get("skill"), str)
    )
