"""Least-privilege manifest validation (SECURITY-CONTRACT §Manifest i12).

Release validation for the ``permissions:`` block in plugin.yaml. Fails
(absents included) when the declaration is absent, malformed, broader than
the least-privilege ceiling, or internally inconsistent. Imported by
tests/test_security_contract.py; also usable as a pre-release check.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: Least-privilege ceiling — anything beyond these is a broader grant.
CANONICAL_READ_PATHS = frozenset(
    {
        "$HERMES_HOME/skills",
        "$HERMES_HOME/profiles/*/skills",
    }
)
CANONICAL_WRITE_PATHS = frozenset(
    {
        "$HERMES_HOME/config.yaml::skills.owner_routing",
        "$HERMES_HOME/skill-owner-routing/state.json",
        "$HERMES_HOME/skills/.skill_owner_findings.json",
    }
)
CANONICAL_DATA_READS = frozenset(
    {"allowlisted-frontmatter", "ownership-drift-ledger", "policy"}
)
CANONICAL_DATA_WRITES = frozenset({"policy", "resolution-status", "audit-state"})


class PermissionDeclarationError(ValueError):
    """Raised when the permissions declaration fails validation."""


def _require_str_list(value: Any, where: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PermissionDeclarationError(f"{where} must be a list of strings")
    if len(set(value)) != len(value):
        raise PermissionDeclarationError(f"{where} contains duplicate entries (inconsistent)")
    return value


def _check_path_shape(path: str, where: str) -> None:
    if ".." in path:
        raise PermissionDeclarationError(f"{where}: {path!r} escapes the home with '..'")
    if not path.startswith("$HERMES_HOME"):
        raise PermissionDeclarationError(f"{where}: {path!r} is not anchored at $HERMES_HOME")
    star_segments = [seg for seg in path.split("/") if "*" in seg]
    if len(star_segments) > 1 or ("*" in path and star_segments != ["*"]):
        raise PermissionDeclarationError(f"{where}: {path!r} uses a wildcard beyond one full segment")


def validate_permissions(manifest: Dict[str, Any]) -> None:
    """Validate the ``permissions`` declaration; raise on any violation.

    Fail modes (all release-blocking): block absent; wrong types/shape
    (malformed); any grant beyond the canonical ceiling (broader — includes
    non-empty ``network``/``secrets`` and ``external_transmission: true``);
    duplicates or ill-formed paths (inconsistent).
    """
    perms = manifest.get("permissions")
    if not isinstance(perms, dict):
        raise PermissionDeclarationError("permissions block is absent or not a mapping")

    fs = perms.get("filesystem")
    if not isinstance(fs, dict):
        raise PermissionDeclarationError("permissions.filesystem is absent or not a mapping")
    reads = _require_str_list(fs.get("read"), "permissions.filesystem.read")
    writes = _require_str_list(fs.get("write"), "permissions.filesystem.write")
    for p in reads + writes:
        _check_path_shape(p, "permissions.filesystem")
    broader_reads = [p for p in reads if p not in CANONICAL_READ_PATHS]
    if broader_reads:
        raise PermissionDeclarationError(f"filesystem.read broader than least privilege: {broader_reads}")
    broader_writes = [p for p in writes if p not in CANONICAL_WRITE_PATHS]
    if broader_writes:
        raise PermissionDeclarationError(f"filesystem.write broader than least privilege: {broader_writes}")

    network = perms.get("network")
    if not isinstance(network, list) or network:
        raise PermissionDeclarationError("permissions.network must be an empty list (no egress)")
    secrets = perms.get("secrets")
    if not isinstance(secrets, list) or secrets:
        raise PermissionDeclarationError("permissions.secrets must be an empty list (no secret access)")

    if perms.get("external_transmission") is not False:
        raise PermissionDeclarationError("permissions.external_transmission must be false")

    ds = perms.get("data_scope")
    if not isinstance(ds, dict):
        raise PermissionDeclarationError("permissions.data_scope is absent or not a mapping")
    d_reads = _require_str_list(ds.get("reads"), "permissions.data_scope.reads")
    d_writes = _require_str_list(ds.get("writes"), "permissions.data_scope.writes")
    broader_d_reads = [d for d in d_reads if d not in CANONICAL_DATA_READS]
    if broader_d_reads:
        raise PermissionDeclarationError(f"data_scope.reads broader than least privilege: {broader_d_reads}")
    broader_d_writes = [w for w in d_writes if w not in CANONICAL_DATA_WRITES]
    if broader_d_writes:
        raise PermissionDeclarationError(f"data_scope.writes broader than least privilege: {broader_d_writes}")


def validate_manifest_file(path) -> None:
    """Load a plugin.yaml and validate its permissions block."""
    import yaml

    manifest = yaml.safe_load(open(path, encoding="utf-8").read())
    if not isinstance(manifest, dict):
        raise PermissionDeclarationError("manifest is not a mapping")
    validate_permissions(manifest)
