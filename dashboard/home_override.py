"""Home-override context: pin the engine + policy cache to a specific home.

The engine (skill_owner_routing) and the core config machinery both resolve
"the fleet DEFAULT home" from HERMES_HOME / the real profile root. The
dashboard serves whichever home the web-server process was started for, but
every route in plugin_api.py computes its own anchor (``_default_home()``) —
and tests pin that anchor to a temp fleet. This context makes the engine
follow the same anchor while a route handler runs:

    with _home_override(home):
        engine.drift.scan()

Uses the core set_hermes_home_override token machinery when importable and
falls back to a HERMES_HOME env swap otherwise. Also clears the engine's
mtime caches on exit so a later real-home read can't serve stale temp data.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def _home_override(home: Path) -> Iterator[None]:
    token = None
    try:
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        token = set_hermes_home_override(home)
    except Exception:
        logger.exception(
            "skill-owner-routing: home-override token machinery unavailable "
            "— falling back to the HERMES_HOME env var"
        )
        token = None
    prev_env = os.environ.get("HERMES_HOME")
    if token is None:
        # M7: when the token machinery is unavailable the env var is the
        # only remaining lever, so it must ALWAYS be pinned — including
        # when HERMES_HOME was not already set. The old ``prev_env is not
        # None`` guard silently no-oped in exactly that case, and engine
        # calls then resolved the REAL fleet home instead of the intended
        # one — the wrong-fleet read this module exists to prevent.
        os.environ["HERMES_HOME"] = str(home)
    try:
        yield
    finally:
        if token is not None:
            try:
                reset_hermes_home_override(token)
            except Exception:
                # The process-global override is still in force after a
                # failed reset — every subsequent engine read in this
                # process resolves the WRONG home. Log loudly; best-effort
                # cleanup: clear the override outright (a stray None-reset
                # is strictly safer than a stuck override).
                logger.exception(
                    "skill-owner-routing: FAILED to reset the home-override "
                    "token — clearing the override outright to avoid a "
                    "stuck wrong-home override"
                )
                try:
                    from hermes_constants import set_hermes_home_override

                    set_hermes_home_override(None)
                except Exception:
                    logger.exception(
                        "skill-owner-routing: override is STUCK — subsequent "
                        "engine reads may resolve the wrong home"
                    )
        else:
            # Restore the pre-entry env state exactly — including
            # "unset" (prev_env None), which the old code never touched.
            if prev_env is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_env
        try:
            from skill_owner_routing.common import reset_caches

            reset_caches()
        except Exception:
            logger.exception(
                "skill-owner-routing: engine cache reset failed after the "
                "home override — later reads may serve stale temp-fleet "
                "data until the next reset_caches()"
            )


def _engine_call(module, attr: str, home: Path, submodule: str = ""):
    """Run engine[<submodule>].<attr>() with the home pinned; None on failure.

    Every None-returning branch logs WHY (OCR minor): a blank dashboard
    caused by a missing attr, an import failure, or a raising engine call
    was previously indistinguishable in the field.
    """
    if module is None:
        return None
    target = module.get(submodule) if submodule else module
    if target is None:
        logger.error(
            "skill-owner-routing: engine has no %r submodule — cannot call "
            "%r",
            submodule,
            attr,
        )
        return None
    fn = getattr(target, attr, None)
    if not callable(fn):
        logger.error(
            "skill-owner-routing: engine %r has no callable %r",
            submodule or "module",
            attr,
        )
        return None
    try:
        with _home_override(home):
            return fn()
    except Exception:
        logger.exception(
            "skill-owner-routing: engine call %s.%s() raised",
            submodule or "engine",
            attr,
        )
        return None
