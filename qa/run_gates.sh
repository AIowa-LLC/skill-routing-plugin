#!/usr/bin/env bash
# Gate A/B runner — SPEC-3 evidence capture.
#
# Runs the QA harness with the RUNTIME venv (the python that imports hermes
# core), appends results to qa/matrix.md with the environment fingerprint
# (core commit, plugin commit, OS, Python).
#
# Usage:
#   ./qa/run_gates.sh            # Gate A + B + self-checks + desktop contract
#   ./qa/run_gates.sh tests/test_gate_a.py   # subset (desktop contract still runs)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Interpreter discovery — the QA gates run on the RUNTIME venv (the python
# that imports hermes core). Resolution order:
#   1. $HERMES_VENV_PY (explicit override)
#   2. $SORE_CORE_ROOT/venv/bin/python (pinned core checkout)
#   3. ~/.hermes/hermes-agent/venv/bin/python (default install location)
VENV_PY="${HERMES_VENV_PY:-}"
if [ -z "$VENV_PY" ]; then
    CORE_DIR="${SORE_CORE_ROOT:-$HOME/.hermes/hermes-agent}"
    if [ -x "${CORE_DIR}/venv/bin/python" ]; then
        VENV_PY="${CORE_DIR}/venv/bin/python"
    fi
fi
if [ -z "$VENV_PY" ]; then
    echo "FAIL  no runtime venv found — set HERMES_VENV_PY or SORE_CORE_ROOT to the hermes core checkout (fail-closed)" >&2
    exit 1
fi
TARGETS="${*:-tests/}"

cd "$REPO"

CORE_COMMIT="$(git -C "${HERMES_CORE:-$(dirname "$(dirname "$VENV_PY")")}" rev-parse --short HEAD)"
PLUGIN_COMMIT="$(git rev-parse --short HEAD)"
PLUGIN_DIRTY="$(git status --porcelain | wc -l)"
OS_ID="$(uname -sr)"
PY_VER="$("$VENV_PY" --version | cut -d' ' -f2)"

echo "== skill-owner-routing QA gates =="
echo "core:      ${CORE_COMMIT}"
echo "plugin:    ${PLUGIN_COMMIT} (dirty files: ${PLUGIN_DIRTY})"
echo "platform:  ${OS_ID} / Python ${PY_VER}"
echo "== pytest: ${TARGETS} =="
# M13: run the suite ONCE. The old header embedded a full pytest run in a
# command substitution (echo "== $(...pytest...) ==") and echo's exit
# status hid that first run's failure under set -e — then line two ran
# the whole suite again. One run, its own exit status is fatal.
"$VENV_PY" -m pytest $TARGETS --tb=short -q

# -- Desktop plugin contract (SPEC-2 surface) --------------------------------
# Fail-closed: a missing node runtime or missing test file is a gate FAILURE,
# never a silent skip — the contract must actually execute here.
CONTRACT_TEST="tests/desktop-plugin-contract.mjs"
echo "== desktop plugin contract (${CONTRACT_TEST}) =="
if ! command -v node >/dev/null 2>&1; then
    echo "FAIL  node runtime not found — desktop contract cannot run (fail-closed)"
    exit 1
fi
if [ ! -f "$CONTRACT_TEST" ]; then
    echo "FAIL  ${CONTRACT_TEST} not found — desktop contract cannot run (fail-closed)"
    exit 1
fi
node "$CONTRACT_TEST"
