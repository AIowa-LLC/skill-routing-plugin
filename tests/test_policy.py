"""Policy contract tests (SPEC-1 config contract, SPEC-3 A7/A8 family).

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

import json

from conftest import write_skill  # noqa: F401


def _read_policy():
    from skill_owner_routing import policy

    return policy.read_policy()


class TestPolicyContract:
    def test_absent_key_means_enabled(self, fleet):
        # A8b: key absent + plugin installed → ENABLED (default-ON posture)
        assert _read_policy()["enabled"] is True

    def test_explicit_false_disables(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing:\n    enabled: false\n", encoding="utf-8"
        )
        assert _read_policy()["enabled"] is False

    def test_explicit_true_enables_with_subkeys(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n"
            "  owner_routing:\n"
            "    enabled: true\n"
            "    require_owner_metadata: false\n"
            "    route_from_default: false\n",
            encoding="utf-8",
        )
        pol = _read_policy()
        assert pol == {
            "enabled": True,
            "require_owner_metadata": False,
            "route_from_default": False,
        }

    def test_partial_key_uses_defaults(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing:\n    route_from_default: false\n",
            encoding="utf-8",
        )
        pol = _read_policy()
        assert pol["enabled"] is True  # absent sub-key keeps default-ON
        assert pol["route_from_default"] is False
        assert pol["require_owner_metadata"] is True

    def test_boolean_shorthand(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing: true\n", encoding="utf-8"
        )
        pol = _read_policy()
        assert pol["enabled"] is True
        assert pol["route_from_default"] is True

    def test_malformed_key_falls_back_to_defaults(self, fleet):
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing: [garbage]\n", encoding="utf-8"
        )
        assert _read_policy()["enabled"] is True

    def test_policy_is_read_from_default_home_not_profile(self, fleet, monkeypatch):
        # A7: a specialist weakening its own config does NOT weaken the rule
        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing:\n    enabled: true\n", encoding="utf-8"
        )
        fleet["trt"].joinpath("config.yaml").write_text(
            "skills:\n  owner_routing:\n    enabled: false\n", encoding="utf-8"
        )
        import hermes_constants

        # Simulate running inside the trt profile home
        monkeypatch.setenv("HERMES_HOME", str(fleet["trt"]))
        pol = _read_policy()
        assert pol["enabled"] is True  # fleet rule from DEFAULT home wins
        monkeypatch.setenv("HERMES_HOME", str(fleet["root"]))

    def test_mtime_cache_invalidates_on_change(self, fleet):
        import time

        cfg = fleet["root"].joinpath("config.yaml")
        cfg.write_text(
            "skills:\n  owner_routing:\n    enabled: true\n", encoding="utf-8"
        )
        assert _read_policy()["enabled"] is True
        time.sleep(0.01)
        cfg.write_text(
            "skills:\n  owner_routing:\n    enabled: false\n", encoding="utf-8"
        )
        assert _read_policy()["enabled"] is False


class TestConcurrentReads:
    """M4 — _load() must run under _LOCK (double-checked locking).

    _load() flips process-global home-override state; concurrent cold-cache
    readers running it outside the lock interleave set/reset (wrong-home
    reads, stuck override) and redundantly repeat the load. Post-fix, a
    cache miss loads once under the lock and every other waiter gets the
    cached dict.
    """

    def test_cold_cache_stampede_loads_once(self, fleet, monkeypatch):
        import threading
        import time

        from skill_owner_routing import policy

        calls = []
        real_load = policy._load

        def slow_load(home, path):
            calls.append(threading.get_ident())
            time.sleep(0.08)  # widen the window where the cache is still empty
            return real_load(home, path)

        monkeypatch.setattr(policy, "_load", slow_load)

        results = {}
        errors = []

        def reader(i):
            try:
                results[i] = policy.read_policy()
            except Exception as exc:  # pragma: no cover — surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
            time.sleep(0.01)  # stagger so later threads miss the cache too
        for t in threads:
            t.join()

        assert not errors
        assert len(calls) == 1, f"expected exactly one load, got {len(calls)}"
        assert len({id(r) for r in results.values()}) == 4  # independent dicts
        assert all(r["enabled"] is True for r in results.values())


class TestUnparseableConfigWarns:
    """Minor (policy.py:86-87 family) — a config that exists but cannot be
    honored must never degrade to defaults SILENTLY. Core's load_config
    never raises on broken YAML (it serves defaults), so a corrupt file
    that says ``enabled: false`` used to silently re-ENABLE routing. The
    degrade-to-defaults posture stays (a typo must not freeze mutations)
    but an unparseable config now logs a loud warning naming the file;
    a merely-absent key stays silent (documented default-ON divergence).
    """

    def test_broken_yaml_that_says_disabled_warns(self, fleet, caplog):
        import logging

        fleet["root"].joinpath("config.yaml").write_text(
            "skills:\n"
            "  owner_routing:\n"
            "    enabled: false\n"
            "  other: [unclosed\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.policy"):
            pol = _read_policy()
        assert pol["enabled"] is True  # degrade-to-defaults posture kept
        assert any(
            "NOT in effect" in r.getMessage() and "config.yaml" in r.getMessage()
            for r in caplog.records
        )

    def test_absent_key_stays_silent(self, fleet, caplog):
        import logging

        # config exists, parses, key absent — the documented divergence
        # must not cry wolf on every standard install.
        fleet["root"].joinpath("config.yaml").write_text(
            "model: grok\n", encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING, logger="skill_owner_routing.policy"):
            pol = _read_policy()
        assert pol["enabled"] is True
        assert not [r for r in caplog.records if "NOT in effect" in r.getMessage()]
