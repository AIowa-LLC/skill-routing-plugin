#!/usr/bin/env bash
# Gate A/B runner — SPEC-3 evidence capture.
#
# Runs the QA harness with the RUNTIME venv (the python that imports hermes
# core), appends results to qa/matrix.md with the environment fingerprint
# (core commit, plugin commit, OS, Python).
#
# Usage:
#   ./qa/run_gates.sh            # Gate A + B + self-checks
#   ./qa/run_gates.sh tests/test_gate_a.py   # subset
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="${HERMES_VENV_PY:-/home/tony/.hermes/hermes-agent/venv/bin/python}"
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
echo "== $("$VENV_PY" -m pytest $TARGETS --tb=short -q) =="
"$VENV_PY" -m pytest $TARGETS --tb=short -q
