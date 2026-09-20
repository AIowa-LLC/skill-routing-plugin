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

# M14: the fingerprint must name the core the suite ACTUALLY ran against.
# conftest resolves the core as SORE_CORE_ROOT → ~/.hermes/hermes-agent;
# the fingerprint uses the same resolution. The old code read an env var
# nothing else in the project used and fell back to the venv's parent
# dir, which could fingerprint a checkout the tests never touched.
CORE_DIR="${SORE_CORE_ROOT:-$HOME/.hermes/hermes-agent}"
CORE_COMMIT="$(git -C "${CORE_DIR}" rev-parse --short HEAD)"
PLUGIN_COMMIT="$(git rev-parse --short HEAD)"
PLUGIN_DIRTY="$(git status --porcelain | wc -l)"
OS_ID="$(uname -sr)"
PY_VER="$("$VENV_PY" --version | cut -d' ' -f2)"

echo "== skill-owner-routing QA gates =="
echo "core:      ${CORE_COMMIT}"
echo "plugin:    ${PLUGIN_COMMIT} (dirty files: ${PLUGIN_DIRTY})"
echo "platform:  ${OS_ID} / Python ${PY_VER}"

# SPEC-3 §Evidence & process: results are APPENDED to qa/matrix.md per run
# with the environment fingerprint. This used to be stdout-only — the spec
# promised the append and Run-5 evidence had to be assembled by hand.
# Appended AFTER a fully green run (a failed run leaves no false PASS row).
MATRIX="qa/matrix.md"
GATES_RUN_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "== pytest: ${TARGETS} =="
# M13: run the suite ONCE, keep the failure fatal. The old header embedded
# a full pytest run in a command substitution (echo "== $(...pytest...) ==")
# and echo's exit status hid that first run's failure under set -e — then
# line two ran the whole suite again. Capturing the output for the matrix
# append keeps the same fatality: under set -e the assignment itself
# propagates pytest's failure and the script dies before any append.
TEST_OUTPUT="$("$VENV_PY" -m pytest $TARGETS --tb=short -q)"
echo "$TEST_OUTPUT"
SUITE_TAIL="$(printf '%s\n' "$TEST_OUTPUT" | tail -1)"

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
CONTRACT_OUTPUT="$(node "$CONTRACT_TEST")"
echo "$CONTRACT_OUTPUT"
CONTRACT_TAIL="$(printf '%s\n' "$CONTRACT_OUTPUT" | tail -1)"

# -- Matrix append (SPEC-3 §Evidence & process) ------------------------------
# Reached ONLY on a fully green run (set -e died above otherwise): append
# the fingerprinted PASS row the spec promises. No FAIL row is ever written
# by the runner itself — a failed run's evidence is its terminal output.
{
    echo ""
    echo "## Gate run ${GATES_RUN_AT} — plugin ${PLUGIN_COMMIT} (auto-appended)"
    echo ""
    echo "- core: \`${CORE_COMMIT}\` — plugin: \`${PLUGIN_COMMIT}\` (dirty: ${PLUGIN_DIRTY}) — ${OS_ID} / Python ${PY_VER}"
    echo "- suite: \`${SUITE_TAIL}\`"
    echo "- desktop contract: \`${CONTRACT_TAIL}\`"
    echo "- verdict: **PASS** (auto-appended by qa/run_gates.sh; failures never append)"
} >> "$MATRIX"
echo "== matrix row appended to ${MATRIX} =="
