"""Findings ledger — plugin-owned drift findings store in DEFAULT home.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Canonical finding record (SPEC-0 Interface 3 — BINDING):
{id, kind, severity, skill, expected_owner, actual, proposed_fix,
 discovered_at, status}
kind ∈ {drifted, misplaced-global, unknown-owner, duplicate/hoarding, unowned}

Fail-loud contract: a ledger that exists but cannot be read, parsed, or
validated raises ``LedgerError``. Callers must surface the failure; a
blind save after a failed load would permanently destroy
resolved/acknowledged audit history (M5).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KINDS = {
    "drifted",
    "misplaced-global",
    "unknown-owner",
    "duplicate/hoarding",
    "unowned",
}


class LedgerError(RuntimeError):
    """The findings ledger exists but cannot be safely read or validated.

    Raised instead of silently degrading to []: the audit history on disk
    must be repaired (or inspected) before any code path overwrites it.
    """


def ledger_path() -> Path:
    from .common import fleet_default_home

    return fleet_default_home() / "skills" / ".skill_owner_findings.json"


def load_findings() -> List[Dict[str, Any]]:
    """Load findings; raise ``LedgerError`` when the file exists but is
    unreadable, corrupt, or schema-invalid. A missing file (first scan)
    is NOT an error and returns []."""
    path = ledger_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise LedgerError(
            f"skill-owner-routing findings ledger exists but cannot be read "
            f"({type(exc).__name__}: {exc}). Refusing to degrade to an empty "
            f"ledger — the next save would destroy audit history. Inspect or "
            f"repair {path}."
        ) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LedgerError(
            f"skill-owner-routing findings ledger is corrupt (invalid JSON) "
            f"at {path}: {exc}. Refusing to degrade to an empty ledger — "
            f"the next save would destroy audit history. Repair the file or "
            f"remove it deliberately."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise LedgerError(
            f"skill-owner-routing findings ledger at {path} has an invalid "
            f"shape (expected a mapping with a 'findings' list). Refusing "
            f"to degrade to an empty ledger — the next save would destroy "
            f"audit history."
        )
    findings = data["findings"]
    invalid = [f for f in findings if not _valid(f)]
    if invalid:
        preview = json.dumps(invalid[0], default=str)[:120]
        raise LedgerError(
            f"skill-owner-routing findings ledger at {path} contains "
            f"{len(invalid)} invalid record(s) (first: {preview}). Refusing "
            f"to degrade to an empty ledger — the next save would destroy "
            f"audit history."
        )
    return findings


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

    Raises ``LedgerError`` when the existing ledger cannot be loaded —
    the file is left untouched (never a destructive overwrite after a
    failed read).
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
