"""Dashboard /map enumeration — phantom twins, nested visibility, no-mint.

M1: on the STANDARD fleet layout (profiles/default/skills -> ../../skills)
every global skill rendered TWICE (once as a phantom profile:: row) plus a
bogus "default" chip, and in fallback mode each twin pair MINTED a
duplicate/hoarding finding for a single physical file.

m1: glob("*/SKILL.md") only saw top-level skills — category-nested skills
(skills/devops/foo/SKILL.md) were invisible to /map while the engine saw
them.

P4 fix: /map derives its inventory from the engine's own discovery when
importable (single source of truth), and the fallback is a faithful port of
the engine's symlink-resolve-and-skip semantics. These tests pin both.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "dashboard"))

import plugin_api  # noqa: E402
from conftest import TEST_SESSION_HEADER, TEST_SESSION_TOKEN  # noqa: E402

API = "/api/plugins/skill-owner-routing"

SKILL_TMPL = """---
name: {name}
description: {desc}
category: {category}
{extra}---

# {name}

body
"""


def make_skill(
    name: str,
    owner: str | None = None,
    category: str = "test",
    justification: str | None = None,
) -> str:
    extra = ""
    if owner or justification:
        bits = []
        if owner:
            bits.append(f"      owner_profile: {owner}")
        if justification:
            bits.append(f"      global_justification: {justification}")
        extra = "metadata:\n  hermes:\n" + "\n".join(bits) + "\n"
    return SKILL_TMPL.format(name=name, desc=f"{name} skill", category=category, extra=extra)


def write_skill(skills_dir: Path, name: str, category: str | None = None, **fm) -> None:
    d = skills_dir / (category or "") / name if category else skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(make_skill(name, **fm))


@pytest.fixture()
def standard_fleet(tmp_path, monkeypatch):
    """STANDARD fleet layout: default profile symlinks skills onto the global
    skills dir; a real second profile (trt) has its own skills; global skills
    include a category-nested one."""
    home = tmp_path / "hermes-home"
    # global skills (top-level)
    write_skill(home / "skills", "alpha-global", justification="shared-primitive")
    write_skill(home / "skills", "beta-global", justification="control-plane")
    # global skills (category-nested — m1)
    write_skill(home / "skills", "nested-global", category="devops", justification="shared-primitive")
    # standard layout: profiles/default/skills -> ../../skills
    (home / "profiles" / "default").mkdir(parents=True)
    os.symlink("../../skills", home / "profiles" / "default" / "skills")
    # real profile with its own skills dir
    write_skill(home / "profiles" / "trt" / "skills", "trt-owned", owner="trt")
    monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
    yield home


@pytest.fixture()
def std_client(standard_fleet):
    app = FastAPI()
    app.include_router(plugin_api.router, prefix=API)
    with TestClient(app, headers={TEST_SESSION_HEADER: TEST_SESSION_TOKEN}) as tc:
        yield tc


FALLBACK = pytest.mark.parametrize(
    "mode",
    [
        pytest.param("engine", id="engine-present"),
        pytest.param("fallback", id="engine-absent-fallback"),
    ],
)


def _rows(client: TestClient):
    body = client.get(f"{API}/map").json()
    assert body["rows"], "/map returned no rows"
    return body


@pytest.fixture()
def force_fallback(monkeypatch):
    """Genuine fallback: engine loader reports absent (no re-probe rebound)."""
    monkeypatch.setattr(plugin_api, "_load_engine", lambda: None)


class TestPhantomTwinKill:
    """M1 — standard layout: each skill exactly once, no phantom rows/chip."""

    @FALLBACK
    def test_each_skill_appears_exactly_once(self, std_client, mode, monkeypatch):
        if mode == "fallback":
            monkeypatch.setattr(plugin_api, "_load_engine", lambda: None)
        body = _rows(std_client)
        ids = [r["skill_id"] for r in body["rows"]]
        names = [r["name"] for r in body["rows"]]
        assert len(ids) == len(set(ids)), f"duplicate skill_ids: {[i for i in ids if ids.count(i) > 1]}"
        assert len(names) == len(set(names)), f"phantom twin rows: {[n for n in names if names.count(n) > 1]}"
        assert set(names) == {"alpha-global", "beta-global", "nested-global", "trt-owned"}

    @FALLBACK
    def test_no_phantom_profile_rows_and_no_default_chip(self, std_client, mode, monkeypatch):
        if mode == "fallback":
            monkeypatch.setattr(plugin_api, "_load_engine", lambda: None)
        body = _rows(std_client)
        phantom = [r for r in body["rows"] if r["skill_id"].startswith("profile::") and r["name"] in
                   {"alpha-global", "beta-global", "nested-global"}]
        assert not phantom, f"phantom profile:: rows for global skills: {phantom}"
        assert "default" not in body["meta"]["profiles"], (
            f"bogus 'default' chip: profiles={body['meta']['profiles']}"
        )
        assert body["meta"]["profiles"] == ["trt"]

    @FALLBACK
    def test_scope_of_global_skills_is_global(self, std_client, mode, monkeypatch):
        if mode == "fallback":
            monkeypatch.setattr(plugin_api, "_load_engine", lambda: None)
        body = _rows(std_client)
        by_name = {r["name"]: r for r in body["rows"]}
        for g in ("alpha-global", "beta-global", "nested-global"):
            assert by_name[g]["location"]["scope"] == "global"
            assert by_name[g]["location"]["profile"] is None
        assert by_name["trt-owned"]["location"]["profile"] == "trt"


class TestNestedVisibility:
    """m1 — category-nested skills visible in /map rows."""

    @FALLBACK
    def test_nested_global_skill_visible(self, std_client, mode, monkeypatch):
        if mode == "fallback":
            monkeypatch.setattr(plugin_api, "_load_engine", lambda: None)
        body = _rows(std_client)
        names = {r["name"] for r in body["rows"]}
        assert "nested-global" in names

    def test_nested_profile_skill_visible(self, tmp_path, monkeypatch):
        home = tmp_path / "hermes-home"
        write_skill(home / "profiles" / "trt" / "skills", "deep-skill", category="devops", owner="trt")
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix=API)
        with TestClient(app, headers={TEST_SESSION_HEADER: TEST_SESSION_TOKEN}) as client:
            body = client.get(f"{API}/map").json()
        names = {r["name"] for r in body["rows"]}
        assert "deep-skill" in names

    def test_cross_home_alias_not_double_counted(self, tmp_path, monkeypatch):
        """A skill-dir symlink resolving outside its scanned home is an
        alias, not a copy — counted once at the canonical home (engine
        semantics, d4effac), in both engine and fallback modes."""
        home = tmp_path / "hermes-home"
        write_skill(home / "skills", "shared-prim", justification="shared-primitive")
        write_skill(home / "profiles" / "trt" / "skills", "trt-owned", owner="trt")
        # alias: trt/skills/shared-prim -> global shared-prim (resolves outside trt)
        alias = home / "profiles" / "trt" / "skills" / "shared-prim"
        alias.parent.mkdir(parents=True, exist_ok=True)
        os.symlink("../../../skills/shared-prim", alias)
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix=API)
        with TestClient(app, headers={TEST_SESSION_HEADER: TEST_SESSION_TOKEN}) as client:
            body = client.get(f"{API}/map").json()
        names = [r["name"] for r in body["rows"]]
        assert names.count("shared-prim") == 1, f"alias double-counted: {body['rows']}"
        scopes = {r["name"]: r["location"]["scope"] for r in body["rows"]}
        assert scopes["shared-prim"] == "global"


class TestFallbackNoMint:
    """M1 (fallback leg) — fallback mode on the standard layout must mint
    ZERO duplicate/hoarding findings."""

    def test_route_level_zero_duplicate_findings(self, std_client, force_fallback):
        std_client.get(f"{API}/map")  # populate state
        body = std_client.get(f"{API}/drift").json()
        dupes = [f for f in body["findings"] if f["kind"] == "duplicate/hoarding"]
        assert not dupes, f"fallback minted duplicate/hoarding findings: {dupes}"

    def test_unit_level_fallback_scan_no_mint(self, standard_fleet, force_fallback):
        scan = plugin_api._scan_fleet(standard_fleet)
        dupes = [f for f in scan["findings"] if f["kind"] == "duplicate/hoarding"]
        assert not dupes, f"fallback scan minted duplicate/hoarding findings: {dupes}"
        assert {r["name"] for r in scan["rows"]} == {
            "alpha-global", "beta-global", "nested-global", "trt-owned",
        }

    def test_fallback_inventory_matches_engine_inventory(self, standard_fleet, monkeypatch):
        """The fallback port must agree with the engine's own discovery on
        the standard layout (same names, same scopes, same profiles)."""
        real_engine = plugin_api._load_engine()
        assert real_engine is not None, "engine must be importable in this repo"
        engine_enum = plugin_api._enumerate(standard_fleet)
        monkeypatch.setattr(plugin_api, "_load_engine", lambda: None)
        local_enum = plugin_api._enumerate(standard_fleet)
        assert local_enum["profiles"] == engine_enum["profiles"] == ["trt"]
        assert set(local_enum["copies"]) == set(engine_enum["copies"])
        for name, engine_copies in engine_enum["copies"].items():
            local_copies = local_enum["copies"][name]
            assert len(local_copies) == len(engine_copies) == 1, name
            assert {c["scope"] for c in local_copies} == {c["scope"] for c in engine_copies}


class TestEngineAuthoritative:
    """P4 direction: with the engine present, rows DERIVE from the engine's
    discovery — perturb what the engine returns and the rows must follow."""

    def test_rows_follow_engine_discovery(self, std_client, monkeypatch):
        engine = plugin_api._load_engine()
        assert engine is not None, "engine must be importable in this repo"
        real_all_homes = engine["drift"]._all_homes
        calls = {"n": 0}

        def tracking_all_homes():
            calls["n"] += 1
            return real_all_homes()

        monkeypatch.setattr(engine["drift"], "_all_homes", tracking_all_homes)
        try:
            body = _rows(std_client)
            assert calls["n"] >= 1, "rows did not consult the engine's discovery"
            assert {r["name"] for r in body["rows"]} == {
                "alpha-global", "beta-global", "nested-global", "trt-owned",
            }
        finally:
            monkeypatch.setattr(engine["drift"], "_all_homes", real_all_homes)

    def test_rows_follow_perturbed_engine_discovery(self, std_client, standard_fleet, monkeypatch):
        """Swap the engine's discovery for a double that sees one synthetic
        skill — /map rows must follow the double, not a local re-scan."""
        engine = plugin_api._load_engine()
        assert engine is not None
        # a real skill file the double will report under a synthetic scope
        ghost_md = standard_fleet / "profiles" / "trt" / "skills" / "ghost-skill" / "SKILL.md"
        ghost_md.parent.mkdir(parents=True, exist_ok=True)
        ghost_md.write_text(make_skill("ghost-skill", owner="ghost"))
        real_all_homes = engine["drift"]._all_homes
        real_skills_in = engine["drift"]._skills_in

        def fake_all_homes():
            return [("default", standard_fleet), ("ghost", standard_fleet / "profiles" / "trt")]

        def fake_skills_in(home):
            if Path(home).name == "trt":
                return [("ghost-skill", ghost_md)]
            return real_skills_in(home)

        monkeypatch.setattr(engine["drift"], "_all_homes", fake_all_homes)
        monkeypatch.setattr(engine["drift"], "_skills_in", fake_skills_in)
        try:
            body = _rows(std_client)
            rows_by_id = {r["skill_id"]: r for r in body["rows"]}
            assert "profile::ghost-skill" in rows_by_id, (
                f"rows did not follow perturbed engine discovery: {sorted(rows_by_id)}"
            )
            row = rows_by_id["profile::ghost-skill"]
            assert row["location"]["profile"] == "ghost"
        finally:
            monkeypatch.setattr(engine["drift"], "_all_homes", real_all_homes)
            monkeypatch.setattr(engine["drift"], "_skills_in", real_skills_in)


    def test_dot_profiles_are_not_chips(self, tmp_path, monkeypatch):
        """Engine discovery may report dot-dirs under profiles/ (e.g.
        .deleted, .hardproof); V1 must not render them as profile chips or
        rows — the frozen contract only admits valid profile ids."""
        home = tmp_path / "hermes-home"
        write_skill(home / "skills", "plain", justification="shared-primitive")
        (home / "profiles" / ".hidden").mkdir(parents=True)
        write_skill(home / "profiles" / ".hidden" / "skills", "ghost", owner="trt")
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix=API)
        with TestClient(app, headers={TEST_SESSION_HEADER: TEST_SESSION_TOKEN}) as client:
            body = client.get(f"{API}/map").json()
        assert body["meta"]["profiles"] == []
        assert {r["name"] for r in body["rows"]} == {"plain"}


class TestLegacyGlobStillCoversTopLevel:
    """Guard: the fix must not lose plain top-level skills (old behavior)."""

    def test_legacy_fleet_layout_unchanged(self, tmp_path, monkeypatch):
        home = tmp_path / "hermes-home"
        write_skill(home / "skills", "plain-global", justification="shared-primitive")
        write_skill(home / "profiles" / "trt" / "skills", "trt-owned", owner="trt")
        monkeypatch.setattr(plugin_api, "_default_home", lambda: home)
        app = FastAPI()
        app.include_router(plugin_api.router, prefix=API)
        with TestClient(app, headers={TEST_SESSION_HEADER: TEST_SESSION_TOKEN}) as client:
            body = client.get(f"{API}/map").json()
        by_name = {r["name"]: r for r in body["rows"]}
        assert set(by_name) == {"plain-global", "trt-owned"}
        assert by_name["plain-global"]["location"]["scope"] == "global"
        assert by_name["trt-owned"]["state"] == "clean"
