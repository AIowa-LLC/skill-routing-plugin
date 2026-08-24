"""skill-owner-routing plugin registration.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.

Hermes loads directory plugins as ``hermes_plugins.<slug>`` without adding
the plugin directory to ``sys.path``; this wrapper puts it there so the
``skill_owner_routing`` package imports as top-level (same bootstrap pattern
as the hardproof precedent plugin).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_owner_routing.register import register  # noqa: E402

__all__ = ["register"]
