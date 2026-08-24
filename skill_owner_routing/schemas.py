"""skill_owner_create / skill_owner_audit tool schemas.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

SKILL_OWNER_CREATE_SCHEMA = {
    "name": "skill_owner_create",
    "description": (
        "Create a skill in the profile that owns the capability, via the "
        "owner-routing transaction. Use when the active profile is 'default' "
        "and the SKILL.md declares metadata.hermes.owner_profile for a named "
        "specialist. Writes into the owner's home with scoped HERMES_HOME "
        "override; ledger + usage records land in the owner home."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name (directory name under skills/).",
            },
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md text (frontmatter + body). Frontmatter "
                    "must declare metadata.hermes.owner_profile."
                ),
            },
            "category": {
                "type": "string",
                "description": "Optional category subdirectory.",
            },
        },
        "required": ["name", "content"],
    },
}

SKILL_OWNER_AUDIT_SCHEMA = {
    "name": "skill_owner_audit",
    "description": (
        "Run the ownership-drift watchdog now: scan every profile + global "
        "skills for owner/location drift, unknown owners, duplicates, and "
        "unjustified global placement. Returns findings; PROPOSES fixes, "
        "never auto-mutates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["scan", "list"],
                "description": (
                    "'scan' runs a fresh audit (default); 'list' returns the "
                    "current findings ledger without rescanning."
                ),
            }
        },
    },
}
