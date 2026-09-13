"""Seam adapter — drive the plugin engine the way hermes core does.

Core calls ``pre_tool_call`` callbacks with kwargs (tool_name, args, ...)
and consumes dict directives ({"action": "block"|"approve", "message", ...}
or None). See hermes_cli/plugins.py:_get_pre_tool_call_directive_details.

BUILD-1 has not landed at harness-writing time, so callable names are
resolved tolerantly (candidates in registration-declaration order). When
the engine lands, tighten this file ONLY — tests never change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from qa.contracts import ensure_engine_importable

_HOOK_CANDIDATES = (
    "pre_tool_call",
    "decide",
    "on_pre_tool_call",
    "handle_pre_tool_call",
    "gate_pre_tool_call",
)

_SCAN_CANDIDATES = (
    "scan",
    "scan_fleet",
    "run_scan",
    "drift_scan",
    "audit",
    "run_audit",
)

_ROUTED_CANDIDATES = (
    "routed_create",
    "create_routed",
    "create",
    "skill_owner_create",
    "run",
)


class RecordingContext:
    """Tolerant registration context: records hooks/tools/skills, no-ops the rest."""

    def __init__(self) -> None:
        self.hooks: Dict[str, List[Callable]] = {}
        self.tools: Dict[str, Callable] = {}
        self.skills: List[str] = []
        self.other: List[str] = []

    def register_hook(self, hook_name: str, callback: Callable) -> object:
        self.hooks.setdefault(hook_name, []).append(callback)
        return object()

    def register_tool(self, name: str, handler: Callable, **_: Any) -> object:
        self.tools[name] = handler
        return object()

    def register_skill(self, name: str, *_: Any, **__: Any) -> object:
        self.skills.append(name)
        return object()

    def __getattr__(self, item: str) -> Callable:
        # any other ctx.* registration surface: record + no-op
        def _noop(*args: Any, **kwargs: Any) -> object:
            return object()

        self.other.append(item)
        return _noop


def _registration_context() -> Optional[RecordingContext]:
    """Try to build a RecordingContext by calling the plugin's register()."""
    import importlib

    ensure_engine_importable()
    for modname in (
        "skill_owner_routing.plugin",
        "skill_owner_routing.register",
        "skill_owner_routing",
    ):
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            continue
        register = getattr(mod, "register", None)
        if callable(register):
            ctx = RecordingContext()
            try:
                register(ctx)
                return ctx
            except Exception:
                continue
    return None


def _from_module(module_name: str, candidates: tuple) -> Optional[Callable]:
    import importlib

    ensure_engine_importable()
    try:
        mod = importlib.import_module(module_name)
    except ImportError:
        return None
    for name in candidates:
        attr = getattr(mod, name, None)
        if callable(attr):
            return attr
    return None


def get_pre_tool_call_hook() -> Callable:
    """Return the plugin's pre_tool_call callback (core-compatible seam)."""
    from qa.contracts import MOD_GATE

    cb = _from_module(MOD_GATE, _HOOK_CANDIDATES)
    if cb is None:
        ctx = _registration_context()
        if ctx is not None:
            cbs = ctx.hooks.get("pre_tool_call") or []
            if cbs:
                return cbs[-1]
    if cb is None:
        pytest.fail(
            "engine present but no pre_tool_call seam found on "
            f"{MOD_GATE} (tried {', '.join(_HOOK_CANDIDATES)} and register()); "
            "align tests/hook_driver.py candidates with BUILD-1"
        )
    return cb


def get_drift_scan() -> Callable:
    """Return the fleet drift-scan entrypoint."""
    from qa.contracts import MOD_DRIFT

    fn = _from_module(MOD_DRIFT, _SCAN_CANDIDATES)
    if fn is None:
        ctx = _registration_context()
        if ctx is not None:
            fn = ctx.tools.get("skill_owner_audit")
    if fn is None:
        pytest.fail(
            f"engine present but no drift-scan seam found on {MOD_DRIFT} "
            f"(tried {', '.join(_SCAN_CANDIDATES)} and skill_owner_audit tool); "
            "align tests/hook_driver.py candidates with BUILD-1"
        )
    return fn


def get_routed_create() -> Callable:
    """Return the skill_owner_create routed-create tool handler."""
    from qa.contracts import MOD_ROUTED_CREATE

    fn = _registration_context_tools_first("skill_owner_create")
    if fn is None:
        fn = _from_module(MOD_ROUTED_CREATE, _ROUTED_CANDIDATES)
    if fn is None:
        pytest.fail(
            "engine present but no routed-create seam found "
            "(tried skill_owner_create tool and "
            f"{MOD_ROUTED_CREATE}:{', '.join(_ROUTED_CANDIDATES)}); "
            "align tests/hook_driver.py candidates with BUILD-1"
        )
    return fn


def _registration_context_tools_first(tool_name: str) -> Optional[Callable]:
    ctx = _registration_context()
    if ctx is not None:
        return ctx.tools.get(tool_name)
    return None


def call_hook(cb: Callable, tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Invoke the callback exactly as core's invoke_hook does; normalize result."""
    result = cb(tool_name=tool_name, args=dict(args or {}))
    if result is None:
        return None
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return None
    if not isinstance(result, dict):
        return None
    return result


def is_block(directive: Optional[Dict[str, Any]]) -> bool:
    return bool(directive) and str(directive.get("action", "")).lower() in ("block", "deny")


def block_message(directive: Optional[Dict[str, Any]]) -> str:
    return str((directive or {}).get("message") or "")


def call_tool(handler: Callable, args: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a plugin tool handler with an args dict; normalize dict/JSON out."""
    result = handler(dict(args or {}))
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        pytest.fail(f"plugin tool returned non-dict result: {result!r}")
    return result


def call_scan(scan_fn: Callable, fleet_root: Path) -> List[Dict[str, Any]]:
    """Invoke the drift-scan entrypoint against a temp fleet root.

    Tolerant to either (root) or () signatures; sets HERMES_HOME to the
    fleet root first so profile-relative discovery resolves inside the
    temp fleet. Restores the prior value so callers never inherit the
    temp fleet (order-independence under pytest-randomly).
    """
    import inspect
    import os

    saved = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(fleet_root)
    try:
        try:
            return normalize_findings(scan_fn(fleet_root))
        except TypeError:
            return normalize_findings(scan_fn())
    finally:
        if saved is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = saved


def snapshot_tree_of(root) -> dict:
    """contracts.snapshot_tree re-export (single import point for suites)."""
    from qa.contracts import snapshot_tree

    return snapshot_tree(root)


# ---------------------------------------------------------------------------
# SPEC-0 Interface 3 — canonical finding record shape (binding).
# kind enum per SPEC-0; "unjustified-global" accepted provisionally for B1c
# (SPEC-3 B1c labels the finding kind that way while the SPEC-0 enum calls
# the unowned-global state `unowned` — divergence flagged in qa/matrix.md).
# ---------------------------------------------------------------------------

FINDING_REQUIRED_KEYS = (
    "id",
    "kind",
    "severity",
    "skill",
    "expected_owner",
    "actual",
    "proposed_fix",
    "discovered_at",
    "status",
)

FINDING_KINDS = {
    "drifted",
    "misplaced-global",
    "unknown-owner",
    "duplicate/hoarding",
    "unowned",
    "unjustified-global",  # SPEC-3 B1c label; see note above
}


def normalize_findings(result: Any) -> List[Dict[str, Any]]:
    """Accept findings lists wrapped in dicts ({"findings": [...]}) or bare."""
    if result is None:
        return []
    if isinstance(result, str):
        result = json.loads(result)
    if isinstance(result, dict):
        for key in ("findings", "items", "results", "data"):
            if isinstance(result.get(key), list):
                result = result[key]
                break
    if not isinstance(result, list):
        pytest.fail(f"drift scan returned unrecognized shape: {type(result)}")
    out = []
    for item in result:
        if isinstance(item, str):
            item = json.loads(item)
        out.append(item)
    return out


def assert_valid_finding(finding: Dict[str, Any]) -> None:
    missing = [k for k in FINDING_REQUIRED_KEYS if k not in finding]
    assert not missing, f"finding missing SPEC-0 Interface 3 keys: {missing}: {finding}"
    assert finding["kind"] in FINDING_KINDS, f"unknown finding kind: {finding['kind']!r}"
