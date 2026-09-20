"""Policy read: skills.owner_routing from the DEFAULT profile config.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

DIVERGENCE FROM CORE PR #87101 (intentional, documented in README): this
plugin treats the key ABSENT as ENABLED — installing the plugin is the
opt-in (Tony ruling: default-ON once installed, user-disable via explicit
``skills.owner_routing.enabled: false``). Core treats absent = disabled.
Both honor an explicit value identically.

CORE-GENERATION BOUNDARY: on cores ≥ #87101, core ships
``skills.owner_routing.enabled: False`` in its defaults, merged through
``load_config``. The plugin's absent-key=ENABLED posture is structurally
unreachable on those cores — fleets opt in via explicit
``skills.owner_routing.enabled: true``. The plugin's own DEFAULTS dict
only governs pre-#87101 cores where the key is genuinely absent from the
config file.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

# module_name -> (mtime_ns, size, policy_dict)
_CACHE: Dict[str, Any] = {}
_LOCK = threading.Lock()

DEFAULTS: Dict[str, Any] = {
    "enabled": True,  # absent key = ENABLED (plugin opt-in posture)
    "require_owner_metadata": True,
    "route_from_default": True,
}


def _config_path(default_home) -> Any:
    from pathlib import Path

    return Path(default_home) / "config.yaml"


def read_policy() -> Dict[str, Any]:
    """Read the fleet policy from DEFAULT-home config.yaml (mtime-cached).

    Reads happen under a DEFAULT-home override so a specialist's own config
    can never weaken the fleet rule (SPEC-1 config contract).
    """
    from .common import fleet_default_home

    home = fleet_default_home()
    path = _config_path(home)
    key = str(path)
    try:
        stat = path.stat()
        fingerprint = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        fingerprint = None  # no config file → all defaults

    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == fingerprint:
            return dict(cached[1])
        # Load UNDER the lock (double-checked locking, OCR M4): _load()
        # mutates process-global home-override state (set/reset token, and
        # a process-global env fallback on cores without the token
        # machinery) — concurrent dashboard threadpool readers interleaving
        # set/reset outside a lock can read the wrong home (SPEC-1) or
        # leave the override stuck on. Loads are rare (cache miss only),
        # so serializing them is free.
        policy = _load(home, path)
        _CACHE[key] = (fingerprint, dict(policy))
        return policy


def _warn_if_unparseable(path) -> None:
    """Loud warning when config.yaml exists but is not parseable YAML.

    Only called on the raw-is-None degrade path, so a normal absent key
    never warns (that is the documented default-ON divergence). Uses the
    raw file, not core's load_config — core never raises on broken YAML
    (serves defaults), which is exactly why the caller can't tell.
    """
    import yaml

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            parsed = yaml.safe_load(fh)
        if not isinstance(parsed, dict):
            logger.warning(
                "skill-owner-routing: %s is not a YAML mapping; the "
                "skills.owner_routing policy cannot be read from it. "
                "Applying default-ON policy — an explicit disable in this "
                "file is NOT in effect. Fix the config file.",
                path,
            )
    except FileNotFoundError:
        pass  # vanished between stat and open — next read treats as absent
    except OSError:
        logger.warning(
            "skill-owner-routing: %s is unreadable; the "
            "skills.owner_routing policy cannot be read from it. "
            "Applying default-ON policy — an explicit disable in this "
            "file is NOT in effect. Fix the config file permissions.",
            path,
        )
    except yaml.YAMLError as exc:
        logger.warning(
            "skill-owner-routing: %s failed to parse (%s); the "
            "skills.owner_routing policy cannot be read from it. "
            "Applying default-ON policy — an explicit disable in this "
            "file is NOT in effect. Fix the config file.",
            path,
            type(exc).__name__,
        )


def _load(home, path) -> Dict[str, Any]:
    defaults = dict(DEFAULTS)
    try:
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )
        from hermes_cli.config import load_config

        token = set_hermes_home_override(home)
        try:
            cfg = load_config() or {}
        finally:
            reset_hermes_home_override(token)
        raw = cfg.get("skills", {})
        raw = raw.get("owner_routing") if isinstance(raw, dict) else None
    except Exception:
        return defaults

    if raw is None:
        # Minor (OCR policy.py:86-87 family): "key absent from a parseable
        # file" and "config exists but cannot be honored" are different
        # situations. Core's load_config never raises on broken YAML — it
        # serves defaults — so a corrupt file that SAYS `enabled: false`
        # would silently re-ENABLE routing (default-ON divergence applied
        # to a file the user did edit). Degrade-to-defaults posture stays
        # (a config typo must not freeze all skill mutations), but never
        # silently: an unparseable config gets a loud warning naming it.
        if path.exists():
            _warn_if_unparseable(path)
        return defaults  # key absent → ENABLED (divergence, see module doc)
    if isinstance(raw, bool):
        return {**defaults, "enabled": raw}
    if not isinstance(raw, dict):
        return defaults

    from utils import is_truthy_value

    enabled_raw = raw.get("enabled")
    return {
        "enabled": is_truthy_value(enabled_raw, default=True),
        "require_owner_metadata": is_truthy_value(
            raw.get("require_owner_metadata"), default=True
        ),
        "route_from_default": is_truthy_value(
            raw.get("route_from_default"), default=True
        ),
    }
