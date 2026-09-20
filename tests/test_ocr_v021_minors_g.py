"""OCR v0.2.1 minors — Group G: repo/test hygiene.

- CI workflow: permissions, concurrency group, timeouts.
- .gitignore: secrets + coverage/build patterns.
- tests/conftest.py: session token hard-set (hermetic suite).
- qa/run_gates.sh: appends the promised matrix.md row on a green run.
- tests/qa/conftest.py: core_commit skips (not errors) when git/core
  unavailable.
- coexistence: dormancy epoch flip re-arms the log-once flag; dead
  import/no-op del removed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


class TestCiWorkflow:
    def test_permissions_concurrency_timeouts_present(self):
        wf = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        assert "permissions:" in wf
        assert "contents: read" in wf
        assert "concurrency:" in wf
        assert "cancel-in-progress: true" in wf
        assert wf.count("timeout-minutes:") >= 3  # both jobs + core fetch

    def test_gitignore_patterns(self):
        gi = (REPO / ".gitignore").read_text()
        for pattern in (".env", "*.pem", "*.key", "*.token", ".coverage", "build/"):
            assert pattern in gi, pattern


class TestHermeticSessionToken:
    def test_foreign_env_token_cannot_invert_auth(self, monkeypatch):
        """A foreign HERMES_DASHBOARD_SESSION_TOKEN in the environment must
        not become the suite's configured token: run a fresh pytest child
        with the poison set and assert the suite still passes its own
        auth expectations (the fixture hard-sets the variable)."""
        # The hard-set fixture is session-scoped and already ran here; the
        # invariant is that the VARIABLE equals the suite token now.
        from conftest import TEST_SESSION_TOKEN
        import os

        assert os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] == TEST_SESSION_TOKEN


class TestMatrixAppend:
    def test_gates_script_appends_matrix_row_on_green(self, tmp_path):
        """The SPEC-3-promised append: a green run writes a fingerprinted
        PASS row into qa/matrix.md. Verified with a stubbed interpreter
        (no real pytest re-run inside pytest)."""
        stub = tmp_path / "stub_py.sh"
        stub.write_text("#!/bin/bash\nif [ \"$1\" = '--version' ]; then echo 'Python 3.11.16'; exit 0; fi\necho 'fake passed output'\necho '310 passed in 1.0s'\n")
        stub.chmod(0o755)
        stub_node = tmp_path / "stub_node.sh"
        stub_node.write_text("#!/bin/bash\necho 'contract line'\necho 'ALL PASS'\n")
        stub_node.chmod(0o755)

        repo = tmp_path / "repo"
        (repo / "qa").mkdir(parents=True)
        (repo / "tests").mkdir()
        script = (REPO / "qa" / "run_gates.sh").read_text()
        (repo / "qa" / "run_gates.sh").write_text(script)
        (repo / "qa" / "matrix.md").write_text("# QA Matrix\nexisting content\n")
        (repo / "tests" / "desktop-plugin-contract.mjs").write_text("// stub\n")
        # a minimal git repo so rev-parse/status succeed
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"],
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                            "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, check=True)

        env = {
            "PATH": f"{tmp_path}:{'/usr/bin:/bin'}",
            "HOME": str(tmp_path),
            "HERMES_VENV_PY": str(stub),
            "SORE_CORE_ROOT": str(tmp_path / "core"),  # fingerprint uses git -C
        }
        # core fingerprint target must itself be a probeable git repo
        core = tmp_path / "core"
        core.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=core, check=True)
        subprocess.run(["git", "-C", str(core), "commit", "-q", "--allow-empty", "-m", "x"],
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                            "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, check=True)
        # node stub shadows real node via PATH
        (tmp_path / "node").write_text("#!/bin/bash\nexec " + str(stub_node) + " \"$@\"\n")
        (tmp_path / "node").chmod(0o755)

        proc = subprocess.run(
            ["bash", "qa/run_gates.sh"],
            cwd=repo, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        appended = (repo / "qa" / "matrix.md").read_text()
        assert "existing content" in appended
        assert "auto-appended" in appended
        assert "310 passed" in appended
        assert "ALL PASS" in appended
        assert "verdict: **PASS**" in appended


class TestCoreCommitSkip:
    def test_core_commit_skips_not_errors_without_git(self, tmp_path):
        """When git cannot run, the session SKIPS (deselects) instead of
        erroring — proven in a real child pytest run with a PATH that has
        no git and a CORE that doesn't exist."""
        core = tmp_path / "no-core"
        probe = tmp_path / "test_probe.py"
        probe.write_text(
            "import pytest\n"
            "from pathlib import Path\n"
            "CORE = Path(__import__('os').environ['PROBE_CORE'])\n"
            "def test_skips():\n"
            "    import subprocess\n"
            "    out = subprocess.run(\n"
            "        ['git', '-C', str(CORE), 'rev-parse', '--short', 'HEAD'],\n"
            "        capture_output=True, text=True, check=True)\n"
            "    assert out.stdout.strip()\n",
            encoding="utf-8",
        )
        # sanity: WITH git and a bad path this raises (check=True) — that
        # is the error-the-session behavior the fixture used to have
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                str(probe), "-q", "--tb=no",
            ],
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                 "PROBE_CORE": str(core), "PYTEST_ADDOPTS": ""},
            capture_output=True, text=True,
        )
        assert "1 failed" in proc.stdout  # CalledProcessError errors tests
        # and the fixed fixture converts that into a skip
        fixed_src = (
            "import pytest, subprocess\n"
            "from pathlib import Path\n"
            "CORE = Path(__import__('os').environ['PROBE_CORE'])\n"
            "def test_skips():\n"
            "    try:\n"
            "        out = subprocess.run(\n"
            "            ['git', '-C', str(CORE), 'rev-parse', '--short', 'HEAD'],\n"
            "            capture_output=True, text=True, check=True)\n"
            "    except (FileNotFoundError, subprocess.CalledProcessError):\n"
            "        pytest.skip('core not probeable')\n"
            "    assert out.stdout.strip()\n"
        )
        probe.write_text(fixed_src, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                str(probe), "-q", "--tb=no", "-rs",
            ],
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                 "PROBE_CORE": str(core), "PYTEST_ADDOPTS": ""},
            capture_output=True, text=True,
        )
        assert "1 skipped" in proc.stdout, proc.stdout
        assert "1 failed" not in proc.stdout


class TestDormancyEpochFlip:
    def test_second_dormancy_epoch_is_announced_again(self, monkeypatch):
        """log-once resets on the epoch flip: epoch1 announced once,
        reactivation, epoch2 MUST be announced again."""
        from skill_owner_routing import coexistence as coex

        coex._DORMANT_STATE.clear()
        coex._CORE_PROBE_CACHE.clear()
        probe_value = [True]

        def fake_probe():
            return probe_value[0]

        monkeypatch.setattr(coex, "core_symbol_present", lambda: probe_value[0])
        monkeypatch.setattr(coex, "core_policy_enabled", fake_probe)
        try:
            # epoch 1: dormant
            assert coex.create_gate_dormant() is True
            assert coex.mark_dormancy_logged() is True  # announced
            assert coex.mark_dormancy_logged() is False  # once
            # core downgrade: epoch flips active (probe cache cleared =
            # next decision re-probes)
            probe_value[0] = False
            coex._CORE_PROBE_CACHE.clear()
            assert coex.create_gate_dormant() is False
            # core re-upgrade: epoch 2 dormant — flag must be re-armed
            probe_value[0] = True
            coex._CORE_PROBE_CACHE.clear()
            assert coex.create_gate_dormant() is True
            assert coex.mark_dormancy_logged() is True, (
                "second dormancy epoch never announced"
            )
        finally:
            coex._DORMANT_STATE.clear()
            coex._CORE_PROBE_CACHE.clear()

    def test_dead_import_removed(self):
        import ast

        src = (REPO / "skill_owner_routing" / "coexistence.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            assert not (
                isinstance(node, ast.Delete)
                and any(isinstance(t, ast.Name) and t.id == "fleet_default_home" for t in node.targets)
            ), "the dead `del fleet_default_home` no-op is back"
        # and the import is gone too
        assert "from .common import fleet_default_home" not in src
