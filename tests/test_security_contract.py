"""SECURITY-CONTRACT.md implementation tests (P7).

Covers, per contract section:
  §REST auth        — matrix over all ten templates: no credential=401,
                      wrong=401, correct=normal, absent env=not public;
                      host request.state flags accepted; REST query tokens
                      never accepted.
  §WebSocket        — Origin allowlist matrix, auth required, ticket
                      single-use + expiry, gated-mode token refusal.
  §Detail redaction — allowlist frontmatter, no body, no absolute paths,
                      fixed history events.
  §Gate fail-closed — the five normative regression tests.
  §M11              — directory and file symlinks out of root: no index,
                      no read, no response.
  §Dependencies i11 — pyproject declares yaml/fastapi/pydantic.
  §Manifest i12     — least-privilege permissions block + validator.

Copyright (c) 2026 skill-owner-routing contributors. MIT licensed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboard"))

import plugin_api  # noqa: E402
from conftest import TEST_SESSION_HEADER, TEST_SESSION_TOKEN  # noqa: E402

AUTH = {TEST_SESSION_HEADER: TEST_SESSION_TOKEN}
API = "/api/plugins/skill-owner-routing"

#: All ten protected REST templates (SECURITY-CONTRACT §REST).
ROUTES = [
    ("get", "/map"),
    ("get", "/map/{skill_id}"),
    ("get", "/drift"),
    ("get", "/drift/summary"),
    ("post", "/audit/run"),
    ("get", "/audit/runs/{run_id}"),
    ("get", "/policy"),
    ("put", "/policy"),
    ("post", "/drift/{finding_id}/resolve"),
]


def _route_args(method: str, template: str, fleet_home: Path) -> dict:
    """Concrete args for each template so the auth check is reached (it
    runs as a dependency BEFORE any handler/validation semantics)."""
    template = template.replace("{skill_id}", "global::clean-global")
    template = template.replace("{run_id}", "deadbeef")
    template = template.replace("{finding_id}", "f_nosuch")
    kwargs: dict = {}
    if method in ("post", "put"):
        kwargs["json"] = (
            {"enabled": True} if method == "put" else {}
        )
    return {"path": f"{API}{template}", **kwargs}


def make_skill(name: str, owner: str | None = None, justification: str | None = None) -> str:
    extra = ""
    if owner or justification:
        bits = []
        if owner:
            bits.append(f"      owner_profile: {owner}")
        if justification:
            bits.append(f"      global_justification: {justification}")
        extra = "metadata:\n  hermes:\n" + "\n".join(bits) + "\n"
    return (
        f"---\nname: {name}\ndescription: {name} skill\ncategory: test\n{extra}---\n\n# {name}\n\nbody\n"
    )


@pytest.fixture()
def sec_fleet(tmp_path, monkeypatch):
    home = tmp_path / "sec-home"
    (home / "skills" / "clean-global").mkdir(parents=True)
    (home / "skills" / "clean-global" / "SKILL.md").write_text(
        make_skill("clean-global", justification="shared-primitive")
    )
    monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
    yield home


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(plugin_api.router, prefix=API)
    return app


def _client(app: FastAPI, headers: dict | None = None) -> TestClient:
    return TestClient(app, headers=headers if headers is not None else AUTH)


# ---------------------------------------------------------------------------
# §REST authentication — matrix over all ten templates
# ---------------------------------------------------------------------------


class TestRestAuthMatrix:
    def test_no_credential_is_401_on_every_route(self, sec_fleet):
        with _client(_app(), headers={}) as client:
            for method, template in ROUTES:
                args = _route_args(method, template, sec_fleet)
                res = getattr(client, method)(args.pop("path"), **args)
                assert res.status_code == 401, f"{method} {template}: {res.status_code}"

    def test_wrong_credential_is_401_on_every_route(self, sec_fleet):
        wrong = {TEST_SESSION_HEADER: "totally-wrong-token-0000000000000000000000"}
        with _client(_app(), headers=wrong) as client:
            for method, template in ROUTES:
                args = _route_args(method, template, sec_fleet)
                res = getattr(client, method)(args.pop("path"), **args)
                assert res.status_code == 401, f"{method} {template}: {res.status_code}"

    def test_missing_and_wrong_are_indistinguishable(self, sec_fleet):
        app = _app()
        with _client(app, headers={}) as anon, _client(app, headers={TEST_SESSION_HEADER: "x" * 64}) as bad:
            a = anon.get(f"{API}/map")
            b = bad.get(f"{API}/map")
            assert a.status_code == b.status_code == 401
            assert a.json() == b.json()

    def test_correct_credential_reaches_every_route(self, sec_fleet):
        with _client(_app()) as client:
            client.get(f"{API}/map")  # populate rows/findings
            expectations = {
                ("get", "/map"): 200,
                ("get", "/map/global::clean-global"): 200,
                ("get", "/drift"): 200,
                ("get", "/drift/summary"): 200,
                ("post", "/audit/run"): 202,
                ("get", "/audit/runs/deadbeef"): 404,  # auth passed, unknown run
                ("get", "/policy"): 200,
                ("put", "/policy"): 200,
                ("post", "/drift/f_nosuch/resolve"): 404,  # auth passed, unknown finding
            }
            for (method, path), expected in expectations.items():
                if method == "put":
                    kwargs = {"json": {"enabled": True}}
                elif method == "post":
                    kwargs = {"json": {}}
                else:
                    kwargs = {}
                res = getattr(client, method)(f"{API}{path}", **kwargs)
                assert res.status_code == expected, f"{method} {path}: {res.status_code} != {expected}"

    def test_query_token_never_authenticates(self, sec_fleet):
        with _client(_app(), headers={}) as client:
            res = client.get(f"{API}/map?token={TEST_SESSION_TOKEN}")
            assert res.status_code == 401
            res = client.get(f"{API}/map?token={TEST_SESSION_TOKEN}&x=1")
            assert res.status_code == 401

    def test_absent_env_fails_closed_everywhere(self, sec_fleet, monkeypatch):
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        plugin_api._reset_auth_cache()
        try:
            app = _app()
            # even the correct header cannot authenticate: nothing is public
            with _client(app) as authed, _client(app, headers={}) as anon:
                for client in (authed, anon):
                    for method, template in ROUTES:
                        args = _route_args(method, template, sec_fleet)
                        res = getattr(client, method)(args.pop("path"), **args)
                        assert res.status_code == 401, f"{method} {template}"
        finally:
            os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] = TEST_SESSION_TOKEN
            plugin_api._reset_auth_cache()

    def test_weak_env_token_fails_closed(self, sec_fleet, monkeypatch):
        monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "short")
        plugin_api._reset_auth_cache()
        try:
            with _client(_app(), headers={TEST_SESSION_HEADER: "short"}) as client:
                assert client.get(f"{API}/map").status_code == 401
        finally:
            os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] = TEST_SESSION_TOKEN
            plugin_api._reset_auth_cache()

    def test_host_state_flags_are_accepted(self, sec_fleet):
        """Host-mounted requests the Hermes /api middlewares already
        authenticated (token principal / cookie session) pass through."""
        for flag, value in (("token_authenticated", True), ("session", object())):
            app = _app()

            @app.middleware("http")
            async def stamp(request, call_next, _flag=flag, _value=value):
                setattr(request.state, _flag, _value)
                return await call_next(request)

            with TestClient(app) as client:
                res = client.get(f"{API}/map")
                assert res.status_code == 200, f"{flag}: {res.status_code}"
                assert res.json()["rows"]


# ---------------------------------------------------------------------------
# §WebSocket — Origin allowlist + auth before accept
# ---------------------------------------------------------------------------


class TestWsOriginAuth:
    LOOPBACK_OK = [
        "http://localhost",
        "http://localhost:5173",
        "https://127.0.0.1",
        "http://127.0.0.1:8080",
        "https://[::1]",
        "http://[::1]:9000",
    ]
    REJECTED = [
        "null",
        "",
        "http://evil.example.com",
        "https://localhost.evil.example.com",
        "http://sub.localhost",
        "ftp://localhost",
        "file://localhost",
        "http://user:pass@localhost",
        "http://localhost/app",
        "http://localhost/?x=1",
        "http://localhost#frag",
        "http://:8080",
        "http://localhost:99999999",
        "chrome-extension://abc",
    ]

    @pytest.mark.parametrize("origin", LOOPBACK_OK)
    def test_loopback_origins_connect_with_auth(self, sec_fleet, origin):
        with _client(_app()) as client:
            with client.websocket_connect(
                f"{API}/events?token={TEST_SESSION_TOKEN}", headers={"origin": origin}
            ):
                client.get(f"{API}/map")
                # receiving the invalidate broadcast proves the socket is live

    @pytest.mark.parametrize("origin", REJECTED)
    def test_foreign_or_malformed_origins_reject(self, sec_fleet, origin):
        with _client(_app()) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(
                    f"{API}/events?token={TEST_SESSION_TOKEN}",
                    headers={"origin": origin} if origin else {},
                ):
                    pass

    def test_absent_origin_rejects(self, sec_fleet):
        with _client(_app()) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"{API}/events?token={TEST_SESSION_TOKEN}"):
                    pass

    def test_valid_origin_without_auth_rejects(self, sec_fleet):
        app = _app()
        for url in (f"{API}/events", f"{API}/events?token=wrong", f"{API}/events?ticket=bogus"):
            with TestClient(app) as client:
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect(url, headers={"origin": "http://localhost:5173"}):
                        pass

    def test_public_url_origin_allowed_when_configured(self, sec_fleet):
        (sec_fleet / "config.yaml").write_text(
            yaml.safe_dump({"dashboard": {"public_url": "https://hermes.example.com"}})
        )
        assert plugin_api._ws_origin_allowed("https://hermes.example.com") is True
        assert plugin_api._ws_origin_allowed("https://hermes.example.com:443") is True
        assert plugin_api._ws_origin_allowed("http://hermes.example.com") is False
        assert plugin_api._ws_origin_allowed("https://other.example.com") is False
        assert plugin_api._ws_origin_allowed("https://sub.hermes.example.com") is False

    def test_no_public_url_means_no_nonloopback_origin(self, sec_fleet):
        (sec_fleet / "config.yaml").write_text(yaml.safe_dump({"dashboard": {}}))
        assert plugin_api._ws_origin_allowed("https://hermes.example.com") is False

    # -- standalone tickets -------------------------------------------------

    def test_ticket_connects_and_is_single_use(self, sec_fleet):
        ticket = plugin_api._mint_ws_ticket()
        assert ticket
        app = _app()
        with TestClient(app) as client:
            with client.websocket_connect(
                f"{API}/events?ticket={ticket}", headers={"origin": "http://localhost"}
            ):
                pass
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(
                    f"{API}/events?ticket={ticket}", headers={"origin": "http://localhost"}
                ):
                    pass

    def test_expired_ticket_rejected(self, sec_fleet):
        ticket = plugin_api._mint_ws_ticket()
        # force-expire it in the store
        expires_at, info = plugin_api._WS_TICKETS[ticket]
        plugin_api._WS_TICKETS[ticket] = (expires_at - 3600, info)
        with _client(_app()) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(
                    f"{API}/events?ticket={ticket}", headers={"origin": "http://localhost"}
                ):
                    pass

    def test_gated_host_mode_refuses_session_token(self, sec_fleet, monkeypatch):
        """Gated mode is ticket-only: a leaked ?token= must not grant WS."""
        fake_host = types.ModuleType("hermes_cli.web_server")
        fake_host._SESSION_TOKEN = "host-secret-not-for-ws-000000000000000"
        fake_gate = types.ModuleType("hermes_cli.web_server_chat")
        fake_gate._ws_auth_ok = lambda ws: False
        monkeypatch.setitem(sys.modules, "hermes_cli.web_server", fake_host)
        monkeypatch.setitem(sys.modules, "hermes_cli.web_server_chat", fake_gate)
        app = _app()
        app.state.auth_required = True
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(
                    f"{API}/events?token={TEST_SESSION_TOKEN}",
                    headers={"origin": "http://localhost:5173"},
                ):
                    pass
        # but a valid standalone ticket is accepted even in gated mode
        ticket = plugin_api._mint_ws_ticket()
        with TestClient(app) as client:
            with client.websocket_connect(
                f"{API}/events?ticket={ticket}", headers={"origin": "http://localhost:5173"}
            ):
                pass


# ---------------------------------------------------------------------------
# §Detail redaction
# ---------------------------------------------------------------------------

SECRET_FM = """---
name: leaky
description: Has secrets.
category: test
api_key: sk-super-secret-0123456789abcdef
password: hunter2-password
token: tok-abcdefabcdefabcdef
commands:
  - curl http://169.254.169.254/latest/meta-data/
metadata:
  hermes:
    owner_profile: trt
    global_justification: shared-primitive
    notes: "arbitrary nested value ARBITRARY-NESTED-MARKER"
  random:
    deep: DEEP-NESTED-MARKER
url: https://internal.example.com/secret
---

# leaky

BODY-SECRET-MARKER should never leave the API.
"""


class TestDetailRedaction:
    @pytest.fixture()
    def leaky(self, sec_fleet):
        d = sec_fleet / "profiles" / "trt" / "skills" / "leaky"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(SECRET_FM)
        return d

    def test_detail_leaks_nothing(self, leaky):
        with _client(_app()) as client:
            body = client.get(f"{API}/map/profile::leaky").json()
            raw = json.dumps(body)
        assert body["frontmatter"].get("name") == "leaky"
        assert body["frontmatter"].get("description") == "Has secrets."
        assert body["frontmatter"]["metadata"]["hermes"]["owner_profile"] == "trt"
        assert body["frontmatter"]["metadata"]["hermes"]["global_justification"] == "shared-primitive"
        for marker in (
            "sk-super-secret",
            "hunter2-password",
            "tok-abcdefabcdefabcdef",
            "169.254.169.254",
            "ARBITRARY-NESTED-MARKER",
            "DEEP-NESTED-MARKER",
            "internal.example.com",
            "BODY-SECRET-MARKER",
            "category",
            "api_key",
            "password",
            str(leaky),
            str(leaky.parent),
        ):
            assert marker not in raw, f"leaked {marker!r}"

    def test_detail_location_has_no_path(self, leaky):
        with _client(_app()) as client:
            body = client.get(f"{API}/map/profile::leaky").json()
        assert body["location"]["path"] == "~/<redacted>"
        assert body["location"]["scope"] == "profile"
        assert body["location"]["profile"] == "trt"

    def test_map_rows_carry_no_absolute_paths(self, leaky):
        with _client(_app()) as client:
            raw = client.get(f"{API}/map").text
        assert str(leaky.parent) not in raw
        assert str(leaky) not in raw
        assert '"path":"~/<redacted>"' in raw.replace(" ", "")

    def test_description_capped_at_1024(self, sec_fleet):
        d = sec_fleet / "skills" / "long-desc"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: long-desc\ndescription: {'x' * 5000}\n---\n\nbody\n"
        )
        with _client(_app()) as client:
            body = client.get(f"{API}/map/global::long-desc").json()
        assert len(body["frontmatter"]["description"]) == 1024

    def test_malformed_allowed_values_omitted(self, sec_fleet):
        d = sec_fleet / "skills" / "malformed"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\n"
            "name: malformed\n"
            "description: 123\n"  # non-string → omitted
            "metadata:\n"
            "  hermes:\n"
            "    owner_profile: '../etc/passwd'\n"  # invalid syntax → omitted
            "    global_justification: because-i-said-so\n"  # unknown → omitted
            "---\n\nbody\n"
        )
        with _client(_app()) as client:
            body = client.get(f"{API}/map/global::malformed").json()
        assert "description" not in body["frontmatter"]
        assert "metadata" not in body["frontmatter"]
        assert "passwd" not in json.dumps(body)

    def test_history_only_known_events_and_timestamps(self, leaky, sec_fleet):
        state_path = sec_fleet / "skill-owner-routing" / "state.json"
        with _client(_app()) as client:
            client.get(f"{API}/map")  # ensure state exists
            state = json.loads(state_path.read_text())
        state["history"] = [
            {"skill": "leaky", "event": "resolved: drifted (leaky)", "at": 1700000000000},
            {"skill": "leaky", "event": "audit: open findings refreshed", "at": 1700000000001},
            {"skill": "leaky", "event": "policy: {\"enabled\": false}", "at": 1700000000002},
            {"skill": "leaky", "event": "resolved: not-a-kind (x)", "at": 1700000000003},
            {"skill": "other", "event": "resolved: drifted (other)", "at": 1700000000004},
        ]
        state_path.write_text(json.dumps(state))
        with _client(_app()) as client:
            body = client.get(f"{API}/map/profile::leaky").json()
        assert body["history"] == [{"event": "resolved: drifted", "at": 1700000000000}]

    def test_symlink_target_never_exposed_in_detail(self, sec_fleet, tmp_path):
        """A row whose stored path points outside the fleet root gets no
        frontmatter read at all (M11 at the detail boundary)."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text(
            "---\nname: outside\napi_key: OUTSIDE-SECRET\n---\nOUTSIDE-BODY\n"
        )
        state_path = sec_fleet / "skill-owner-routing" / "state.json"
        with _client(_app()) as client:
            client.get(f"{API}/map")
            state = json.loads(state_path.read_text())
        state["rows"].append(
            {
                "skill_id": "global::ghost",
                "name": "ghost",
                "category": "",
                "owner_profile": None,
                "location": {"scope": "global", "profile": None, "path": str(outside)},
                "state": "clean",
            }
        )
        state_path.write_text(json.dumps(state))
        with _client(_app()) as client:
            body = client.get(f"{API}/map/global::ghost").json()
        assert body["frontmatter"] == {}
        raw = json.dumps(body)
        assert "OUTSIDE-SECRET" not in raw
        assert "OUTSIDE-BODY" not in raw
        assert str(outside) not in raw


# ---------------------------------------------------------------------------
# §M11 — symlink containment in discovery (engine + fallback)
# ---------------------------------------------------------------------------


class TestM11SymlinkContainment:
    def _outside(self, tmp_path):
        outside = tmp_path / "outside-root"
        (outside / "evil-skill").mkdir(parents=True)
        (outside / "evil-skill" / "SKILL.md").write_text(
            "---\nname: evil-skill\ndescription: OUTSIDE-CONTENT-MARKER\n---\nOUTSIDE-BODY\n"
        )
        return outside

    def test_directory_symlink_outside_root_not_indexed(self, sec_fleet, tmp_path):
        os.symlink(self._outside(tmp_path) / "evil-skill", sec_fleet / "skills" / "evil-skill")
        with _client(_app()) as client:
            raw = client.get(f"{API}/map").text
        assert "evil-skill" not in raw
        assert "OUTSIDE-CONTENT-MARKER" not in raw

    def test_file_symlink_outside_root_not_indexed(self, sec_fleet, tmp_path):
        d = sec_fleet / "skills" / "evil-file"
        d.mkdir(parents=True)
        os.symlink(self._outside(tmp_path) / "evil-skill" / "SKILL.md", d / "SKILL.md")
        with _client(_app()) as client:
            raw = client.get(f"{API}/map").text
        assert "evil-file" not in raw
        assert "OUTSIDE-CONTENT-MARKER" not in raw

    def test_file_symlink_rejected_in_fallback_mode_too(self, sec_fleet, tmp_path, monkeypatch):
        monkeypatch.setattr(plugin_api, "_ENGINE_CACHE", [None, False])
        d = sec_fleet / "skills" / "evil-file2"
        d.mkdir(parents=True)
        os.symlink(self._outside(tmp_path) / "evil-skill" / "SKILL.md", d / "SKILL.md")
        try:
            with _client(_app()) as client:
                raw = client.get(f"{API}/map").text
            assert "evil-file2" not in raw
            assert "OUTSIDE-CONTENT-MARKER" not in raw
        finally:
            monkeypatch.undo()

    def test_engine_discovery_rejects_file_symlink(self, fleet, tmp_path, monkeypatch):
        from skill_owner_routing import drift

        outside = tmp_path / "outside-engine"
        (outside / "evil").mkdir(parents=True)
        (outside / "evil" / "SKILL.md").write_text(
            "---\nname: evil\ndescription: OUTSIDE-ENGINE-MARKER\n---\nbody\n"
        )
        d = fleet["root"] / "skills" / "evil-link"
        d.mkdir(parents=True)
        os.symlink(outside / "evil" / "SKILL.md", d / "SKILL.md")
        found = drift._skills_in(fleet["root"])
        names = [name for name, _md in found]
        assert "evil-link" not in names
        assert all("OUTSIDE-ENGINE-MARKER" not in md.read_text() for _, md in found)

    def test_legitimate_local_skills_still_indexed(self, sec_fleet):
        with _client(_app()) as client:
            raw = client.get(f"{API}/map").text
        assert "clean-global" in raw


# ---------------------------------------------------------------------------
# §Gate — fail-closed semantics (normative regression names)
# ---------------------------------------------------------------------------


def _gate_call(action="create", name="some-skill", content=None, **extra):
    from skill_owner_routing.gate import pre_tool_call

    args = {"action": action, "name": name}
    if content is not None:
        args["content"] = content
    args.update(extra)
    return pre_tool_call(tool_name="skill_manage", args=args)


class TestGateFailClosed:
    def test_unexpected_gate_exception_blocks_fail_closed(self, enabled_config, monkeypatch):
        from skill_owner_routing import gate

        def boom(**kwargs):
            raise RuntimeError("sensitive-secret-boom")

        monkeypatch.setattr(gate, "_decide", boom)
        result = _gate_call(content="---\nname: x\n---\nbody\n")
        assert result is not None
        assert result["action"] == "block"
        assert result["message"].startswith("skill-owner-routing gate error (fail-closed):")
        assert result.get("error_code") == "gate-error"

    def test_gate_error_message_distinguishes_gate_malfunction_from_policy_violation(
        self, enabled_config, monkeypatch
    ):
        from skill_owner_routing import gate

        monkeypatch.setattr(gate, "_decide", lambda *a, **k: (_ for _ in ()).throw(ValueError("v")))
        err = _gate_call(content="---\nname: x\n---\nbody\n")["message"]
        monkeypatch.undo()
        pol = _gate_call(content="---\nname: plain\n---\nbody\n")["message"]
        assert "gate malfunction, not a policy violation" in err
        assert "gate error" in err
        assert "gate error" not in pol
        assert "gate malfunction" not in pol
        assert "owner_profile" in pol  # action-specific guidance retained

    def test_gate_error_does_not_expose_exception_text_or_sensitive_inputs(
        self, enabled_config, monkeypatch
    ):
        from skill_owner_routing import gate

        def boom(*args, **kwargs):
            raise RuntimeError("sensitive-secret-boom /etc/passwd")

        monkeypatch.setattr(gate, "_decide", boom)
        result = _gate_call(name="my-secret-skill-name", content="SECRET-CONTENT-MARKER")
        msg = result["message"]
        assert "sensitive-secret-boom" not in msg
        assert "/etc/passwd" not in msg
        assert "my-secret-skill-name" not in msg
        assert "SECRET-CONTENT-MARKER" not in msg
        assert "RuntimeError" in msg  # only the safe exception class

    def test_policy_violation_message_is_not_labeled_gate_error(self, enabled_config):
        result = _gate_call(content="---\nname: plain\n---\nbody\n")
        assert result["action"] == "block"
        assert "gate error" not in result["message"]
        assert result.get("error_code") == "policy-violation"
        # action-specific guidance retained
        assert "owner_profile" in result["message"]

    def test_non_skill_manage_early_bail_still_performs_zero_io(self, monkeypatch):
        import builtins

        def fail(*args, **kwargs):
            raise AssertionError("early-bail path performed I/O")

        monkeypatch.setattr("pathlib.Path.stat", fail)
        monkeypatch.setattr("pathlib.Path.exists", fail)
        monkeypatch.setattr(builtins, "open", fail)
        from skill_owner_routing.gate import pre_tool_call

        assert pre_tool_call(tool_name="terminal", args={"command": "ls"}) is None
        assert pre_tool_call(tool_name="read_file", args={"path": "/etc"}) is None
        assert pre_tool_call(tool_name="", args={}) is None

    def test_unreadable_target_blocks_fail_closed(self, enabled_config, monkeypatch):
        """Unreadable required input on a mutation: block, never allow."""
        from skill_owner_routing import gate, index

        skill_md = enabled_config["trt"] / "skills" / "trt-skill" / "SKILL.md"
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        skill_md.write_text("---\nname: trt-skill\n---\nbody\n")

        real_read = Path.read_text

        def failing_read(self, *a, **k):
            if self.name == "SKILL.md":
                raise OSError("permission denied")
            return real_read(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", failing_read)
        monkeypatch.setattr(index, "lookup", lambda name: skill_md)
        result = _gate_call(action="edit", name="trt-skill")
        assert result["action"] == "block"
        assert result["message"].startswith("skill-owner-routing gate error (fail-closed):")
        assert result.get("error_code") == "gate-error"


# ---------------------------------------------------------------------------
# §i11 dependencies + §i12 manifest
# ---------------------------------------------------------------------------


class TestDependenciesDeclared:
    def test_pyproject_declares_runtime_deps(self):
        import tomllib

        data = tomllib.loads((REPO / "pyproject.toml").read_text())
        deps = " ".join(data["project"]["dependencies"]).lower()
        for expected in ("pyyaml", "fastapi", "pydantic"):
            assert expected in deps, f"pyproject missing {expected}"
        # constraints compatible with the Hermes web extra
        assert "fastapi>=0.104" in deps.replace(" ", "")
        assert "<1" in data["project"]["dependencies"][1]

    def test_plugin_api_imports_with_declared_deps(self):
        """The dashboard module imports cleanly with only the declared
        dependency set present (the running venv provides exactly these)."""
        spec = importlib.util.spec_from_file_location(
            "plugin_api_smoke", REPO / "dashboard" / "plugin_api.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "router")


class TestManifestLeastPrivilege:
    @staticmethod
    def _permissions():
        manifest = yaml.safe_load((REPO / "plugin.yaml").read_text())
        return manifest.get("permissions")

    def test_permissions_block_present_and_least_privilege(self):
        perms = self._permissions()
        assert isinstance(perms, dict), "permissions block absent"
        fs = perms["filesystem"]
        assert sorted(fs["read"]) == [
            "$HERMES_HOME/profiles/*/skills",
            "$HERMES_HOME/skills",
        ]
        assert sorted(fs["write"]) == [
            "$HERMES_HOME/config.yaml::skills.owner_routing",
            "$HERMES_HOME/skill-owner-routing/state.json",
            "$HERMES_HOME/skills/.skill_owner_findings.json",
        ]
        assert perms["network"] == []
        assert perms["secrets"] == []
        assert perms["external_transmission"] is False
        ds = perms["data_scope"]
        assert sorted(ds["reads"]) == ["allowlisted-frontmatter", "ownership-drift-ledger", "policy"]
        assert sorted(ds["writes"]) == ["audit-state", "policy", "resolution-status"]

    def test_validator_rejects_absent_block(self):
        from security_validator import PermissionDeclarationError, validate_permissions

        with pytest.raises(PermissionDeclarationError):
            validate_permissions({"name": "skill-owner-routing"})

    def test_validator_rejects_broader_scope(self):
        import copy

        import yaml as _yaml

        from security_validator import PermissionDeclarationError, validate_permissions

        manifest = _yaml.safe_load((REPO / "plugin.yaml").read_text())
        # broader read (arbitrary path under home)
        broader = copy.deepcopy(manifest)
        broader["permissions"]["filesystem"]["read"].append("$HERMES_HOME")
        with pytest.raises(PermissionDeclarationError):
            validate_permissions(broader)
        # network egress declared
        net = copy.deepcopy(manifest)
        net["permissions"]["network"] = ["https://exfil.example.com"]
        with pytest.raises(PermissionDeclarationError):
            validate_permissions(net)
        # secrets declared
        sec = copy.deepcopy(manifest)
        sec["permissions"]["secrets"] = ["vault"]
        with pytest.raises(PermissionDeclarationError):
            validate_permissions(sec)
        # external transmission
        tx = copy.deepcopy(manifest)
        tx["permissions"]["external_transmission"] = True
        with pytest.raises(PermissionDeclarationError):
            validate_permissions(tx)
        # escaping path
        esc = copy.deepcopy(manifest)
        esc["permissions"]["filesystem"]["read"].append("$HERMES_HOME/../secrets")
        with pytest.raises(PermissionDeclarationError):
            validate_permissions(esc)
        # malformed (wrong type)
        mal = copy.deepcopy(manifest)
        mal["permissions"]["filesystem"]["read"] = "not-a-list"
        with pytest.raises(PermissionDeclarationError):
            validate_permissions(mal)
        # the shipped manifest itself validates clean
        validate_permissions(manifest)


# ---------------------------------------------------------------------------
# §Desktop — SDK-only transport
# ---------------------------------------------------------------------------


class TestDesktopTransport:
    def test_no_raw_fetch_or_token_globals(self):
        src = (REPO / "desktop" / "plugin.js").read_text()
        assert "fetch(" not in src.replace("restRef(", "").replace("refetch()", "")
        assert "__HERMES_SESSION_TOKEN__" not in src
        assert "window.__HERMES" not in src
