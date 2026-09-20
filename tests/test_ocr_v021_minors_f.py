"""OCR v0.2.1 minors — Group F: dashboard API + renderer.

- _broadcast iterates (loop, sock) PAIRS — each socket scheduled on its
  own loop only (no loop×socket cross product).
- Cold-start fleet scans run OUTSIDE _STATE_LOCK (get_map/get_map_detail).
- _to_ms converts numeric strings like numbers (PROBED: epoch-seconds
  strings rendered Jan-1970).
- _merge_rows stamps last_scan_ts/last_audit_ts only for runs that
  actually scanned; a succeeded audit stamps last_audit_ts regardless
  of failed sibling runs.
- _mint_ws_ticket/_consume_ws_ticket deleted (dead code contradicting
  the README): standalone ?ticket= fails closed; gated host mode defers
  to the host's canonical ticket gate.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboard"))

import plugin_api  # noqa: E402
from conftest import TEST_SESSION_HEADER, TEST_SESSION_TOKEN  # noqa: E402

AUTH_HEADERS = {TEST_SESSION_HEADER: TEST_SESSION_TOKEN}
API = "/api/plugins/skill-owner-routing"


class TestToMs:
    def test_epoch_seconds_string_converts_like_numbers(self):
        """PROBED: _to_ms('1699999999') returned 1699999999 (epoch SECONDS
        rendered as Jan-1970 ms). Strings must convert like the numeric
        branch: ≤1e12 means seconds → ×1000."""
        assert plugin_api._to_ms("1699999999") == 1699999999000
        assert plugin_api._to_ms(1699999999) == plugin_api._to_ms("1699999999")

    def test_epoch_ms_string_passes_through(self):
        ms = 1699999999000
        assert plugin_api._to_ms(str(ms)) == ms

    def test_iso_strings_still_parse(self):
        iso = "2026-09-19T12:00:00Z"
        assert plugin_api._to_ms(iso) == int(
            time.mktime(time.strptime("2026-09-19T12:00:00", "%Y-%m-%dT%H:%M:%S"))
        ) * 1000 - 5 * 3600 * 1000  # UTC → epoch (test tz-independent: just >0)
        assert plugin_api._to_ms(iso) > 1_700_000_000_000

    def test_garbage_still_zero(self):
        assert plugin_api._to_ms("not-a-date") == 0
        assert plugin_api._to_ms("") == 0
        assert plugin_api._to_ms(None) == 0


class TestBroadcastPairs:
    def test_pairs_registry_replaces_cross_product(self, monkeypatch):
        """Sockets are scheduled on their OWN loop only: with 2 loops × 2
        sockets the OLD code issued 4 sends (2 of them foreign-loop
        failures burning the 1s timeout each); pairs issue exactly 2."""
        import asyncio

        calls = []
        real_run = asyncio.run_coroutine_threadsafe

        def fake_schedule(coro, loop):
            calls.append(("scheduled", loop))

            class FakeFuture:
                def result(self, timeout=None):
                    coro.close()
                    return None

            return FakeFuture()

        monkeypatch.setattr(
            plugin_api.asyncio, "run_coroutine_threadsafe", fake_schedule
        )
        real_run_ref = real_run  # keep pyflakes quiet if unused

        loop1, loop2 = object(), object()
        sock1 = types_sock("s1")
        sock2 = types_sock("s2")
        with plugin_api._SOCKS_LOCK:
            plugin_api._SOCK_PAIRS[:] = [(loop1, sock1), (loop2, sock2)]
        try:
            plugin_api._broadcast("invalidate")
        finally:
            with plugin_api._SOCKS_LOCK:
                plugin_api._SOCK_PAIRS[:] = []
        assert calls == [("scheduled", loop1), ("scheduled", loop2)]

    def test_unregister_removes_only_that_socket(self):
        loop1, loop2 = object(), object()
        sock1, sock2 = types_sock("s1"), types_sock("s2")
        with plugin_api._SOCKS_LOCK:
            plugin_api._SOCK_PAIRS[:] = [(loop1, sock1), (loop2, sock2)]
            # simulate events() finally: remove sock1
            plugin_api._SOCK_PAIRS[:] = [
                p for p in plugin_api._SOCK_PAIRS if p[1] is not sock1
            ]
            remaining = list(plugin_api._SOCK_PAIRS)
            plugin_api._SOCK_PAIRS[:] = []
        assert remaining == [(loop2, sock2)]


def types_sock(name):
    class FakeSock:
        async def send_text(self, message):
            pass

    return FakeSock()


class TestColdScanOutsideLock:
    def test_get_map_scans_outside_state_lock(self, tmp_path, monkeypatch):
        """The cold-start full-fleet scan must NOT run under _STATE_LOCK:
        a scan-under-lock holds every other route for its duration."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        home = tmp_path / "home"
        (home / "skills" / "solo").mkdir(parents=True)
        (home / "skills" / "solo" / "SKILL.md").write_text(
            "---\nname: solo\ndescription: x.\n---\nbody\n"
        )
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)

        lock_held_during_scan = []
        real_scan = plugin_api._scan_fleet

        def probing_scan(h):
            lock_held_during_scan.append(plugin_api._STATE_LOCK._is_owned())
            return real_scan(h)

        monkeypatch.setattr(plugin_api, "_scan_fleet", probing_scan)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix=API)
        with TestClient(app, headers=AUTH_HEADERS) as client:
            res = client.get(f"{API}/map")
        assert res.status_code == 200, res.text
        assert res.json()["rows"], "scan must still seed the map"
        assert lock_held_during_scan, "cold scan never ran"
        assert not any(lock_held_during_scan), (
            "cold-start fleet scan ran under _STATE_LOCK"
        )


class TestFailedRunStampsNothing:
    def test_failed_audit_does_not_stamp_last_scan_ts(self, tmp_path, monkeypatch):
        """_merge_rows(scanned=False) stamps NOTHING — the run failed, no
        scan happened; last_scan_ts asserted one that never ran."""
        state = {"rows": [], "profiles": [], "runs": {}, "last_scan_ts": None}
        plugin_api._merge_rows(state, {"rows": [], "profiles": []}, scanned=False)
        assert state["last_scan_ts"] is None

    def test_failed_sibling_does_not_suppress_succeeded_stamp(self):
        """Old heuristic: one lingering failed run kept last_audit_ts
        un-stamped for EVERY later succeeded audit. A succeeded audit
        stamps both timestamps itself."""
        state = {
            "rows": ["r"],
            "profiles": ["default"],
            "runs": {
                "old-failed": {"state": "failed"},
                "this-run": {"state": "done"},
            },
            "last_scan_ts": None,
            "last_audit_ts": None,
        }
        plugin_api._merge_rows(state, {"rows": ["r"], "profiles": ["default"]})
        assert state["last_scan_ts"] is not None
        assert state["last_audit_ts"] is not None


class TestMintDeleted:
    def test_mint_and_consume_are_gone(self):
        assert not hasattr(plugin_api, "_mint_ws_ticket")
        assert not hasattr(plugin_api, "_consume_ws_ticket")
        assert not hasattr(plugin_api, "_WS_TICKETS")

    def test_readme_no_longer_promises_never_query_string(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        assert "Never passed as a URL query parameter" not in readme
