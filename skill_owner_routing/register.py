"""Plugin registration: ONE pre_tool_call hook + two tools.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    """Register the enforcement surface with Hermes."""
    from .gate import pre_tool_call
    from .schemas import SKILL_OWNER_AUDIT_SCHEMA, SKILL_OWNER_CREATE_SCHEMA
    from . import routed_create

    ctx.register_hook("pre_tool_call", pre_tool_call)

    def skill_owner_create(args: dict, **_kwargs: Any) -> str:
        name = str(args.get("name") or "")
        content = str(args.get("content") or "")
        category = args.get("category")
        if not name or not content:
            return json.dumps(
                {"success": False, "error": "name and content are required."},
                ensure_ascii=False,
            )
        return routed_create.routed_create(
            name=name,
            content=content,
            category=str(category) if category else None,
        )

    def skill_owner_audit(args: dict, **_kwargs: Any) -> str:
        from . import drift, ledger

        action = str(args.get("action") or "scan")
        if action == "list":
            return json.dumps(
                {"ok": True, "findings": ledger.load_findings()},
                ensure_ascii=False,
            )
        return drift.run_audit()

    ctx.register_tool(
        name="skill_owner_create",
        toolset="skill-owner-routing",
        schema=SKILL_OWNER_CREATE_SCHEMA,
        handler=skill_owner_create,
        description=str(SKILL_OWNER_CREATE_SCHEMA["description"]),
        emoji="🧭",
    )
    ctx.register_tool(
        name="skill_owner_audit",
        toolset="skill-owner-routing",
        schema=SKILL_OWNER_AUDIT_SCHEMA,
        handler=skill_owner_audit,
        description=str(SKILL_OWNER_AUDIT_SCHEMA["description"]),
        emoji="🔎",
    )
    logger.info("skill-owner-routing registered: gate hook + 2 tools")
